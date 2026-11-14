'use strict';
'require view';
'require ui';
'require fs';
'require uci';

/*
 * 首次配置向导（纯前端 + /usr/libexec/wizard/*.sh）。
 *
 * 重型逻辑全部在 shell 里：apply.sh 负责备份、uci 暂存、场景脚本、提交、
 * 90 秒确认窗口。前端只做采集、基本校验和展示结果，方便在 SSH 下复现同样操作。
 */
return view.extend({
	load: function() {
		return uci.load('wizard').then(function() {
			return {
				done: uci.get('wizard', 'main', 'done') === '1',
				confirmed: uci.get('wizard', 'main', 'confirmed') === '1'
			};
		}).catch(function() {
			return { done: false, confirmed: false };
		});
	},

	render: function(data) {
		var self = this;
		var step = 1;
		var state = {
			scenario: 'router-pppoe',
			lan_ip: '192.168.1.1',
			lan_mask: '255.255.255.0',
			gateway: '',
			dns1: '',
			dns2: '',
			wan_user: '',
			wan_pass: '',
			root_pass: '',
			proxy: 'homeproxy',
			sub_url: '',
			agh: '1',
			fullcone: '1',
			tailscale: '0',
			ddns: '0'
		};

		var body = E('div', { 'class': 'cbi-section' });
		var footer = E('div', { 'class': 'cbi-page-actions' });

		var title = E('h2', {}, [ '初始化向导' ]);
		var indicator = E('div', { 'class': 'cbi-section-descr' }, [ '' ]);

		function field(label, input, hint) {
			return E('div', { 'class': 'cbi-value' }, [
				E('label', { 'class': 'cbi-value-title' }, [ label ]),
				E('div', { 'class': 'cbi-value-field' }, [
					input,
					hint ? E('div', { 'class': 'cbi-value-description' }, [ hint ]) : ''
				])
			]);
		}

		function text(id, value, placeholder) {
			return E('input', {
				'id': id,
				'type': 'text',
				'class': 'cbi-input-text',
				'value': value || '',
				'placeholder': placeholder || ''
			});
		}

		function password(id, placeholder) {
			return E('input', {
				'id': id,
				'type': 'password',
				'class': 'cbi-input-text',
				'placeholder': placeholder || ''
			});
		}

		function checkbox(id, label, checked) {
			var input = E('input', {
				'id': id,
				'type': 'checkbox',
				'checked': checked ? 'checked' : null
			});
			return E('div', {}, [
				input,
				E('label', { 'for': id, 'style': 'margin-left:6px' }, [ label ])
			]);
		}

		function radio(name, value, label, checked) {
			var input = E('input', {
				'type': 'radio',
				'name': name,
				'value': value,
				'checked': checked ? 'checked' : null
			});
			return E('div', {}, [ input, E('label', { 'style': 'margin-left:6px' }, [ label ]) ]);
		}

		function collect() {
			var get = function(id) {
				var el = document.getElementById(id);
				return el ? el.value : '';
			};
			state.lan_ip = get('wiz_lan_ip') || state.lan_ip;
			state.lan_mask = get('wiz_lan_mask') || state.lan_mask;
			state.gateway = get('wiz_gateway');
			state.dns1 = get('wiz_dns1');
			state.dns2 = get('wiz_dns2');
			state.wan_user = get('wiz_wan_user');
			state.wan_pass = get('wiz_wan_pass');
			state.root_pass = get('wiz_root_pass');
			state.sub_url = get('wiz_sub_url');
			state.proxy = get('wiz_proxy');
			var cb = function(id) {
				var el = document.getElementById(id);
				return el && el.checked ? '1' : '0';
			};
			state.agh = cb('wiz_agh');
			state.fullcone = cb('wiz_fullcone');
			state.tailscale = cb('wiz_tailscale');
			state.ddns = cb('wiz_ddns');
			var sel = document.querySelector('input[name="wiz_scenario"]:checked');
			if (sel) state.scenario = sel.value;
		}

		function validIPv4(s) {
			return /^(\d{1,3}\.){3}\d{1,3}$/.test(s) &&
				s.split('.').every(function(p) { return +p <= 255; });
		}

		function stepOne() {
			return [
				E('p', {}, [ '选择这台设备在网络中的角色。不确定就选"旁路由"。' ]),
				field('场景', E('div', {}, [
					radio('wiz_scenario', 'router-pppoe', '主路由 · PPPoE 拨号', state.scenario === 'router-pppoe'),
					radio('wiz_scenario', 'router-dhcp', '主路由 · 上级 DHCP', state.scenario === 'router-dhcp'),
					radio('wiz_scenario', 'bypass', '旁路由 · 主路由继续拨号', state.scenario === 'bypass'),
					radio('wiz_scenario', 'travel', '旅行/远程网关', state.scenario === 'travel')
				]))
			];
		}

		function stepTwo() {
			var nodes = [
				field('LAN 地址', text('wiz_lan_ip', state.lan_ip, '192.168.1.2')),
				field('子网掩码', text('wiz_lan_mask', state.lan_mask, '255.255.255.0'))
			];
			if (state.scenario === 'bypass') {
				nodes.push(field('主路由网关', text('wiz_gateway', state.gateway, '192.168.1.1'),
					'旁路由必须指向主路由；本机 DHCP 会被关闭，避免地址冲突。'));
			}
			if (state.scenario === 'router-pppoe') {
				nodes.push(field('PPPoE 账号', text('wiz_wan_user', state.wan_user, '宽带账号')));
				nodes.push(field('PPPoE 密码', password('wiz_wan_pass')));
			}
			nodes.push(field('DNS 1（可选）', text('wiz_dns1', state.dns1, '223.5.5.5')));
			nodes.push(field('DNS 2（可选）', text('wiz_dns2', state.dns2, '119.29.29.29')));
			nodes.push(field('管理密码', password('wiz_root_pass', '至少 8 位'),
				'留空则保持现有密码。'));
			return nodes;
		}

		function stepThree() {
			return [
				field('代理前端', E('div', {}, [
					E('select', { 'id': 'wiz_proxy', 'class': 'cbi-input-select' }, [
						E('option', { 'value': 'homeproxy', 'selected': state.proxy === 'homeproxy' ? 'selected' : null }, [ 'HomeProxy（推荐 ARM）' ]),
						E('option', { 'value': 'passwall', 'selected': state.proxy === 'passwall' ? 'selected' : null }, [ 'PassWall（推荐 x86）' ]),
						E('option', { 'value': 'nikki', 'selected': state.proxy === 'nikki' ? 'selected' : null }, [ 'Nikki（Mihomo）' ]),
						E('option', { 'value': 'off', 'selected': state.proxy === 'off' ? 'selected' : null }, [ '暂不启用' ])
					])
				]), '切换时会停用其它前端，并清理其残留规则。'),
				field('订阅地址', text('wiz_sub_url', state.sub_url, 'https://...'),
					'只写入本机 /etc/config/wizard，不上传、不进仓库。粘贴后请到对应插件页面导入。'),
				E('div', { 'class': 'cbi-value' }, [
					E('label', { 'class': 'cbi-value-title' }, [ '可选功能' ]),
					E('div', { 'class': 'cbi-value-field' }, [
						checkbox('wiz_agh', 'AdGuardHome 去广告（预装不接管 53）', state.agh === '1'),
						checkbox('wiz_fullcone', 'FullCone NAT', state.fullcone === '1'),
						checkbox('wiz_tailscale', 'Tailscale 组网', state.tailscale === '1'),
						checkbox('wiz_ddns', 'DDNS-GO 动态域名', state.ddns === '1')
					])
				])
			];
		}

		function stepFour() {
			var rows = [
				[ '场景', state.scenario ],
				[ 'LAN', state.lan_ip + ' / ' + state.lan_mask ],
				[ '网关', state.gateway || '（不设置）' ],
				[ '代理前端', state.proxy ],
				[ '订阅', state.sub_url ? '已填写（仅本机）' : '未填写' ],
				[ 'AdGuardHome', state.agh === '1' ? '启用' : '不启用' ],
				[ 'FullCone', state.fullcone === '1' ? '启用' : '不启用' ]
			];
			return [
				E('p', {}, [ '确认后应用配置，并在 90 秒内确认；超时自动回滚到应用前的状态。' ]),
				E('table', { 'class': 'table' }, [
					E('tbody', {}, rows.map(function(r) {
						return E('tr', { 'class': 'tr' }, [
							E('td', { 'class': 'td' }, [ r[0] ]),
							E('td', { 'class': 'td' }, [ String(r[1]) ])
						]);
					}))
				])
			];
		}

		function draw() {
			body.innerHTML = '';
			indicator.textContent = '第 ' + step + ' / 4 步';
			var nodes = step === 1 ? stepOne() :
				step === 2 ? stepTwo() :
				step === 3 ? stepThree() : stepFour();
			nodes.forEach(function(n) { body.appendChild(n); });
			drawFooter();
		}

		function drawFooter() {
			footer.innerHTML = '';
			if (step > 1) {
				footer.appendChild(E('button', {
					'class': 'btn',
					'click': function(ev) { ev.preventDefault(); step--; draw(); }
				}, [ '上一步' ]));
			}
			if (step < 4) {
				footer.appendChild(E('button', {
					'class': 'btn cbi-button cbi-button-apply',
					'click': function(ev) {
						ev.preventDefault();
						collect();
						if (step === 2) {
							if (!validIPv4(state.lan_ip)) {
								ui.addNotification(null, E('p', [ 'LAN 地址不合法' ]), 'danger');
								return;
							}
							if (state.scenario === 'bypass' && !validIPv4(state.gateway)) {
								ui.addNotification(null, E('p', [ '旁路由必须填写主路由网关' ]), 'danger');
								return;
							}
							if (state.root_pass && state.root_pass.length < 8) {
								ui.addNotification(null, E('p', [ '管理密码至少 8 位' ]), 'danger');
								return;
							}
						}
						step++;
						draw();
					}
				}, [ '下一步' ]));
			} else {
				footer.appendChild(E('button', {
					'class': 'btn cbi-button cbi-button-apply',
					'click': function(ev) {
						ev.preventDefault();
						collect();
						var args = [
							'scenario=' + state.scenario,
							'lan_ip=' + state.lan_ip,
							'lan_mask=' + state.lan_mask,
							'gateway=' + state.gateway,
							'dns1=' + state.dns1,
							'dns2=' + state.dns2,
							'wan_user=' + state.wan_user,
							'wan_pass=' + state.wan_pass,
							'root_pass=' + state.root_pass,
							'proxy=' + state.proxy,
							'sub_url=' + state.sub_url,
							'agh=' + state.agh,
							'fullcone=' + state.fullcone,
							'tailscale=' + state.tailscale,
							'ddns=' + state.ddns
						];
						ev.target.disabled = true;
						return fs.exec('/usr/libexec/wizard/apply.sh', args).then(function(res) {
							if (res.code !== 0) {
								ui.addNotification(null, E('p', [ '应用失败：' + (res.stderr || res.stdout || '未知错误') ]), 'danger');
								ev.target.disabled = false;
								return;
							}
							self.showConfirm();
						}).catch(function(err) {
							ui.addNotification(null, E('p', [ '应用失败：' + err ]), 'danger');
							ev.target.disabled = false;
						});
					}
				}, [ '应用配置' ]));
			}
		}

		var container = E('div', {}, [ title, indicator, body, footer ]);
		draw();

		if (data.done) {
			container.insertBefore(
				E('div', { 'class': 'alert-message warning' }, [
					'这台设备已完成向导。继续操作会覆盖现有网络配置。'
				]),
				title.nextSibling
			);
		}

		return container;
	},

	showConfirm: function() {
		var left = 90;
		var counter = E('strong', {}, [ String(left) ]);
		var timer = null;

		var dialog = E('div', { 'class': 'cbi-modal' }, [
			E('h3', {}, [ '配置已应用' ]),
			E('p', {}, [ '请在 ', counter, ' 秒内点击确认，否则自动回滚。' ]),
			E('div', { 'class': 'right' }, [
				E('button', {
					'class': 'btn cbi-button cbi-button-apply',
					'click': function(ev) {
						ev.preventDefault();
						clearInterval(timer);
						fs.exec('/usr/libexec/wizard/confirm.sh', []).then(function() {
							document.body.removeChild(dialog);
							ui.addNotification(null, E('p', [ '配置已确认。建议重新登录并检查网络。' ]), 'info');
						});
					}
				}, [ '确认配置' ])
			])
		]);

		document.body.appendChild(dialog);
		timer = setInterval(function() {
			left--;
			counter.textContent = String(left);
			if (left <= 0) {
				clearInterval(timer);
				document.body.removeChild(dialog);
				ui.addNotification(null, E('p', [ '确认超时，配置已回滚。' ]), 'warning');
			}
		}, 1000);
	},

	handleSave: null,
	handleSaveApply: null,
	handleReset: null
});
