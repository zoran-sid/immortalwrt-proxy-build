/* proxy-guard: migrate the 25.12.2 generator output for sing-box 1.14.
 * Injected immediately before writefile(), before removeBlankAttrs().
 * See https://sing-box.sagernet.org/migration/ .
 */
let compat_rules = [];
for (let i = 0; i < length(config.inbounds || []); i++) {
	let inbound = config.inbounds[i];
	if (inbound.sniff) {
		push(compat_rules, {
			inbound: [inbound.tag],
			action: 'sniff',
			timeout: inbound.sniff_timeout
		});
	}
	if (inbound.domain_strategy && inbound.domain_strategy !== 'as_is')
		push(compat_rules, {
			inbound: [inbound.tag], action: 'resolve', strategy: inbound.domain_strategy
		});
	inbound.sniff = null;
	inbound.sniff_override_destination = null;
	inbound.sniff_timeout = null;
	inbound.domain_strategy = null;
}
// The old generator emits an unused block-out even in the default mode.
let compat_blocks = [];
for (let i = 0; i < length(config.outbounds || []); i++) {
	if (config.outbounds[i].type === 'block')
		push(compat_blocks, config.outbounds[i].tag);
}
function compat_is_block(tag) {
	for (let i = 0; i < length(compat_blocks); i++)
		if (compat_blocks[i] === tag) return true;
	return false;
}
function compat_rule(rule) {
	if (compat_is_block(rule.outbound)) {
		rule.outbound = null;
		rule.action = 'reject';
	}
	for (let i = 0; i < length(rule.rules || []); i++)
		compat_rule(rule.rules[i]);
}
config.outbounds = filter(config.outbounds || [], (out) => out.type !== 'block');
for (let i = 0; i < length(config.route.rules || []); i++)
	compat_rule(config.route.rules[i]);
config.route.rules = [...compat_rules, ...(config.route.rules || [])];
if (compat_is_block(config.route.final)) {
	push(config.route.rules, { action: 'reject' });
	config.route.final = null;
}
if (config.route.default_domain_resolver)
	config.route.default_domain_resolver.action = null;
// 1.14 uses independent caches by default and no longer supports RDRC.
if (config.dns) config.dns.independent_cache = null;
if (config.experimental && config.experimental.cache_file) {
	config.experimental.cache_file.store_rdrc = null;
	config.experimental.cache_file.rdrc_timeout = null;
}
