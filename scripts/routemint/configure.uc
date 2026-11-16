// Only modifies an isolated copy. The installer validates it before activation.
import { cursor } from 'uci';
import { readfile, writefile } from 'fs';

const stage = getenv('RM_STAGE');
const plan = json(readfile(getenv('RM_PLAN')));
const ctx = cursor(stage + '/config', stage + '/delta');
const conf = 'homeproxy';
ctx.load(conf) || die('Cannot load staged HomeProxy configuration.');
function get(section, option) { return ctx.get(conf, section, option); }
function set(section, option, value) {
	ctx.set(conf, section, option, value) || die('Cannot set ' + section + '.' + option);
}
function list(value) {
	if (type(value) === 'array') return value;
	return value ? [value] : [];
}
function valid_nodes(nodes) {
	if (!length(nodes)) die('No selected node. Set RM_PROXY_NODE to an existing node ID.');
	for (let i = 0; i < length(nodes); i++)
		if (ctx.get(conf, nodes[i]) !== 'node') die('Unknown node ID: ' + nodes[i]);
	return nodes;
}

// Prefer the current custom default outbound; otherwise use the main node.
let selected = get('config', 'main_node');
let nodes = selected === 'urltest' ? list(get('config', 'main_urltest_nodes')) : list(selected);
let old_default = get('routing', 'default_outbound');
if (get('config', 'routing_mode') === 'custom' && ctx.get(conf, old_default) === 'routing_node') {
	selected = get(old_default, 'node');
	nodes = selected === 'urltest' ? list(get(old_default, 'urltest_nodes')) : list(selected);
}
if (getenv('RM_PROXY_NODE')) nodes = [getenv('RM_PROXY_NODE')];
nodes = valid_nodes(nodes);
const ipv6 = get('config', 'ipv6_support') === '1';
const named_routes = index(readfile('/etc/homeproxy/scripts/generate_client.uc'),
    'proxy-guard: unique tags per routing node') >= 0;
let chosen = {};
for (let i = 0; i < length(plan.groups); i++) {
	let group = plan.groups[i];
	let override = getenv('RM_' + uc(group) + '_NODE');
	chosen[group] = valid_nodes(override ? [override] : nodes);
}

// Remove our previous sections; preserve other custom sections but disable
// them so their order and references cannot override the selected profile.
let remove = [];
ctx.foreach(conf, null, (s) => {
	if (s.routemint === '1') push(remove, s['.name']);
	else if (index(['routing_node', 'routing_rule', 'ruleset', 'dns_server', 'dns_rule'], s['.type']) >= 0)
		set(s['.name'], 'enabled', '0');
});
for (let i = 0; i < length(remove); i++) ctx.delete(conf, remove[i]);
function section(name, kind, label) {
	if (ctx.get(conf, name)) die('Reserved section already exists: ' + name);
	ctx.set(conf, name, kind) || die('Cannot create section: ' + name);
	set(name, 'routemint', '1');
	set(name, 'enabled', '1');
	set(name, 'label', label);
}
for (let i = 0; i < length(plan.groups); i++) {
	let group = plan.groups[i], name = 'rm_' + group;
	section(name, 'routing_node', 'RouteMint ' + group);
	// URLTest wrappers give each policy its own tag, even when policies share
	// a node. The upstream generator otherwise emits duplicate outbound tags.
	if (named_routes && length(chosen[group]) === 1) {
		set(name, 'node', chosen[group][0]);
	} else {
		set(name, 'node', 'urltest');
		set(name, 'urltest_nodes', chosen[group]);
		set(name, 'urltest_url', 'https://www.gstatic.com/generate_204');
		set(name, 'urltest_interval', '300');
	}
}
section('rm_dns_local', 'dns_server', 'RouteMint domestic DNS');
set('rm_dns_local', 'type', 'https');
set('rm_dns_local', 'server', '223.5.5.5');
set('rm_dns_local', 'server_port', '443');
set('rm_dns_local', 'path', '/dns-query');
set('rm_dns_local', 'tls_sni', 'dns.alidns.com');
set('rm_dns_local', 'outbound', 'direct-out');
for (let i = 0; i < length(plan.groups); i++) {
	let group = plan.groups[i], name = 'rm_dns_' + group;
	section(name, 'dns_server', 'RouteMint DNS ' + group);
	set(name, 'type', 'https');
	set(name, 'server', '1.1.1.1');
	set(name, 'server_port', '443');
	set(name, 'path', '/dns-query');
	set(name, 'tls_sni', 'cloudflare-dns.com');
	set(name, 'outbound', 'rm_' + group);
	set('rm_' + group, 'domain_resolver', 'rm_dns_local');
}

let categories = [];
for (let i = 0; i < length(plan.rules); i++) {
	let category = plan.rules[i][0], policy = plan.rules[i][1];
	let key = join('_', split(category, '-'));
	let rs = 'rm_set_' + key, route = 'rm_route_' + key, dns = 'rm_query_' + key;
	push(categories, category);
	section(rs, 'ruleset', 'RouteMint ' + category);
	set(rs, 'type', 'remote');
	set(rs, 'format', 'binary');
	set(rs, 'url', 'https://raw.githubusercontent.com/zoran-sid/RouteMint-Rules/release/rules/' + category + '/' + category + '.srs');
	set(rs, 'outbound', 'rm_proxy');
	set(rs, 'update_interval', '1d');
	section(route, 'routing_rule', 'RouteMint ' + category);
	set(route, 'rule_set', [rs]);
	set(route, 'action', policy === 'reject' ? 'reject' : 'route');
	if (policy !== 'reject') set(route, 'outbound', policy === 'direct' ? 'direct-out' : 'rm_' + policy);
	// IP-only sets do not classify DNS queries. Telegram's DNS follows the
	// proxy default. LAN hostnames remain handled by dnsmasq before forwarding.
	if (index(['private', 'cn-ip', 'cn-ip6', 'telegram'], category) >= 0) continue;
	section(dns, 'dns_rule', 'RouteMint DNS ' + category);
	set(dns, 'rule_set', [rs]);
	set(dns, 'action', policy === 'reject' ? 'reject' : 'route');
	if (policy !== 'reject') set(dns, 'server', policy === 'direct' ? 'rm_dns_local' : 'rm_dns_' + policy);
}
if (!ctx.get(conf, 'routing')) ctx.set(conf, 'routing', 'homeproxy');
if (!ctx.get(conf, 'dns')) ctx.set(conf, 'dns', 'homeproxy');
set('config', 'routing_mode', 'custom');
set('config', 'enabled', '1');
set('routing', 'default_outbound', 'rm_proxy');
set('routing', 'default_outbound_dns', 'rm_dns_local');
set('routing', 'bypass_cn_traffic', '0');
ctx.delete(conf, 'routing', 'domain_strategy');
set('dns', 'default_server', 'rm_dns_proxy');
set('dns', 'default_strategy', ipv6 ? 'prefer_ipv4' : 'ipv4_only');
set('dns', 'disable_cache', '0');
ctx.commit(conf) || die('Cannot commit staged configuration.');
writefile(stage + '/categories', join('\n', categories) + '\n');

let generator = readfile('/etc/homeproxy/scripts/generate_client.uc');
const cursor_line = 'const uci = cursor();';
const write_line = "writefile(RUN_DIR + '/sing-box-c.json',";
if (length(split(generator, cursor_line)) !== 2 || length(split(generator, write_line)) !== 2)
	die('Generator layout changed; preview generation aborted.');
generator = join("const uci = cursor(getenv('RM_STAGE') + '/config', getenv('RM_STAGE') + '/delta');", split(generator, cursor_line));
generator = join("writefile(getenv('RM_STAGE') + '/preview.json',", split(generator, write_line));
writefile(stage + '/generate-preview.uc', generator);
print('Prepared cn-mainland / ads-light: ', length(categories), ' rule sets.\n');
print('Policy groups reuse the current node unless RM_<GROUP>_NODE is specified.\n');
