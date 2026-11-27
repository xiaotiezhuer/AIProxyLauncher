import 'dart:async';
import 'dart:io' show Platform;
import 'dart:math';

// ignore: deprecated_member_use
import 'package:flutter/material.dart';

import '../models/models.dart';
import '../services/local_relay.dart';
import '../services/network_audit.dart';
import '../services/platform_bridge.dart';
import '../services/proxy_engine.dart';
import '../services/region_utils.dart';
import '../services/rollback_service.dart';
import '../services/settings_store.dart';
import 'sheets.dart';
import 'theme.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({
    super.key,
    required this.bridge,
    required this.engine,
    required this.auditService,
    required this.settingsStore,
    required this.relay,
    required this.settings,
    required this.onSettingsChanged,
    this.autoRunOnInit = true,
  });

  final PlatformBridge bridge;
  final MobileProxyEngine engine;
  final NetworkAuditService auditService;
  final SettingsStore settingsStore;
  final LocalProxyRelay relay;
  final AppSettings settings;
  final ValueChanged<AppSettings> onSettingsChanged;
  final bool autoRunOnInit;

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  String _controllerSecret = '';
  String _categoryFilter = 'all';
  bool _checking = false;
  bool _logExpanded = false;
  bool _socksBridgeActive = false;

  DiscoveryResult? _lastResult;
  List<HealthFinding> _lastFindings = const [];
  final List<(String, String)> _logs = [];
  final MobileRollbackService _rollbackService = MobileRollbackService();

  AppPalette get _p => widget.settings.darkMode
      ? AppPalette.darkPalette
      : AppPalette.lightPalette;

  List<AiTarget> get _allTargets {
    final enabledPresets = presetMobileTargets
        .where((p) => widget.settings.enabledPresets.contains(p.id))
        .toList();
    return [
      ...defaultMobileTargets,
      ...enabledPresets,
      ...widget.settings.customTargets,
    ];
  }

  List<AiTarget> get _filteredTargets {
    if (_categoryFilter == 'all') return _allTargets;
    return _allTargets.where((t) => t.category == _categoryFilter).toList();
  }

  @override
  void initState() {
    super.initState();
    if (widget.autoRunOnInit) {
      WidgetsBinding.instance.addPostFrameCallback((_) => _initFlow());
    }
  }

  Future<void> _initFlow() async {
    if (widget.settings.relayEnabled && !widget.relay.running) {
      try {
        await widget.relay.start();
      } catch (_) {}
    }
    if (!mounted) return;
    if (!widget.settings.guideDismissed) {
      await _openGuide();
    } else {
      await _runCheck();
    }
  }

  void _appendLog(String text, {String level = 'info'}) {
    final now = DateTime.now();
    final hh = now.hour.toString().padLeft(2, '0');
    final mm = now.minute.toString().padLeft(2, '0');
    final ss = now.second.toString().padLeft(2, '0');
    setState(() {
      _logs.insert(0, ('[$hh:$mm:$ss] $text', level));
      if (_logs.length > 80) {
        _logs.removeRange(80, _logs.length);
      }
    });
  }

  Future<void> _openGuide() async {
    await showUserGuideSheet(
      context: context,
      palette: _p,
      initialDontShowAgain: widget.settings.guideDismissed,
      onComplete: (dontShowAgain) {
        if (dontShowAgain != widget.settings.guideDismissed) {
          widget.onSettingsChanged(
            widget.settings.copyWith(guideDismissed: dontShowAgain),
          );
        }
        _appendLog('使用指南已收起至右上角「?」按钮，随时点击重阅。', level: 'good');
        if (_lastResult == null && !_checking) {
          unawaited(_runCheck());
        }
      },
    );
  }

  Future<void> _syncRelayRoutes(List<ProxyReport> reports) async {
    final portReports =
        reports.where((r) => r.issue.isEmpty && r.port > 0 && r.host.isNotEmpty).toList();
    final needsSocksBridge = portReports.any((r) => r.scheme == 'socks5');

    if (needsSocksBridge && !widget.relay.running) {
      try {
        await widget.relay.start();
        _socksBridgeActive = true;
      } catch (_) {
        _socksBridgeActive = false;
      }
    } else if (!widget.settings.relayEnabled &&
        !needsSocksBridge &&
        _socksBridgeActive) {
      await widget.relay.stop();
      _socksBridgeActive = false;
    }

    if (!widget.relay.running) return;
    if (portReports.isEmpty) {
      widget.relay.updateRoutes();
      return;
    }

    UpstreamSpec toSpec(ProxyReport r) => UpstreamSpec(
          scheme: r.scheme == 'socks5' ? 'socks5' : 'http',
          host: r.host,
          port: r.port,
        );

    final ordered = List<ProxyReport>.from(portReports)
      ..sort((a, b) {
        final aOk = !isRestrictedRegion(a.region) ? 1 : 0;
        final bOk = !isRestrictedRegion(b.region) ? 1 : 0;
        if (aOk != bOk) return bOk.compareTo(aOk);
        return b.passingCount.compareTo(a.passingCount);
      });

    final hostMap = <String, UpstreamSpec>{};
    for (final target in _allTargets) {
      final best = _bestReportForTarget(target, portReports);
      if (best != null && best.port > 0) {
        final spec = toSpec(best);
        for (final h in target.hosts) {
          hostMap[h.split('/').first] = spec;
        }
      }
    }
    widget.relay.updateRoutes(
      defaultUpstream: toSpec(ordered.first),
      hostUpstreams: hostMap,
      fallbacks: ordered.skip(1).map(toSpec).toList(),
    );
  }

  Future<void> _toggleRelay() async {
    if (widget.settings.relayEnabled) {
      final needsSocks = (_lastResult?.reports ?? const [])
          .any((r) => r.scheme == 'socks5' && r.port > 0);
      widget.onSettingsChanged(widget.settings.copyWith(relayEnabled: false));
      if (needsSocks && widget.relay.running) {
        setState(() => _socksBridgeActive = true);
        _appendLog('已关闭手动统一中继；因当前使用 SOCKS5 代理，本地桥接保持运行。');
      } else {
        await widget.relay.stop();
        setState(() => _socksBridgeActive = false);
        _appendLog('已关闭统一本地中继。');
      }
    } else {
      try {
        final port = await widget.relay.start();
        widget.onSettingsChanged(widget.settings.copyWith(relayEnabled: true));
        setState(() => _socksBridgeActive = false);
        await _syncRelayRoutes(_lastResult?.reports ?? const []);
        _appendLog('已启动统一本地中继 127.0.0.1:$port（支持 HTTP/SOCKS5 上游热切换）。',
            level: 'good');
      } catch (e) {
        _appendLog('无法启动统一本地中继：$e', level: 'error');
      }
    }
  }

  Future<void> _runCheck() async {
    if (_checking) return;
    setState(() => _checking = true);
    _appendLog('开始扫描手机 VPN、本机/局域网代理与 ${_allTargets.length} 项 AI 服务…');
    try {
      final result = await widget.engine.discoverAndCheck(
        targets: _allTargets,
        settings: widget.settings,
        controllerSecret: _controllerSecret,
        excludeRelayPort: widget.relay.running ? widget.relay.port : null,
      );
      final findings = await widget.auditService.diagnoseNetworkHealth(
        networkState: result.networkState,
        reports: result.reports,
      );
      await _syncRelayRoutes(result.reports);
      if (!mounted) return;
      setState(() {
        _lastResult = result;
        _lastFindings = findings;
        _checking = false;
      });
      for (final h in result.hints) {
        _appendLog(h, level: 'warn');
      }
      if (result.reports.isEmpty) {
        _appendLog(
          '未检测到可用代理或非中国大陆出口。请先打开代理 App 连接节点，或在「设置」中填写局域网代理/控制接口。',
          level: 'error',
        );
      } else {
        for (final rep in result.reports) {
          final regTxt = rep.region.isNotEmpty
              ? ' · ${regionLabel(rep.region)}(${rep.region})'
              : '';
          final latTxt = rep.latencyMs != null ? ' · ${rep.latencyMs}ms' : '';
          _appendLog(
            '${rep.provider}${rep.port > 0 ? " :${rep.port}" : " (系统路由)"}$regTxt$latTxt · ${rep.passingCount}/${rep.probes.length} 服务通过',
            level: rep.hasHealthyProbe ? 'good' : 'error',
          );
          if (isRestrictedRegion(rep.region)) {
            _appendLog(
              '地区提醒：当前 AI 出口为 ${regionLabel(rep.region)}（${rep.region}），ChatGPT / Claude / Gemini 可能受限，建议切换美/日/台/新节点。',
              level: 'error',
            );
          }
        }
      }
    } catch (e) {
      if (!mounted) return;
      setState(() => _checking = false);
      _appendLog('检测异常：$e', level: 'error');
    }
  }

  ProxyReport? _bestReportForTarget(AiTarget target, List<ProxyReport> reports) {
    final valid = reports.where((r) => r.issue.isEmpty).toList();
    if (valid.isEmpty) return null;
    valid.sort((a, b) {
      int passingFor(ProxyReport r) =>
          target.hosts.where((h) => r.probes[h]?.passing == true).length;
      final ap = passingFor(a);
      final bp = passingFor(b);
      if (ap != bp) return bp.compareTo(ap);
      final aReg = !isRestrictedRegion(a.region) ? 1 : 0;
      final bReg = !isRestrictedRegion(b.region) ? 1 : 0;
      if (aReg != bReg) return bReg.compareTo(aReg);
      return (b.endpoint != null ? 1 : 0).compareTo(a.endpoint != null ? 1 : 0);
    });
    return valid.first;
  }

  Future<void> _openTarget(AiTarget target) async {
    final ok = await widget.bridge.launchApp(
      androidPackage: target.androidPackage.isNotEmpty ? target.androidPackage : null,
      fallbackAndroidPackages: target.fallbackAndroidPackages,
      iosScheme: target.iosScheme.isNotEmpty ? target.iosScheme : null,
      url: target.webUrl,
    );
    if (!mounted) return;
    if (ok) {
      _appendLog('已唤起 ${target.name}', level: 'good');
    } else {
      _appendLog('未能直接打开 ${target.name}，请检查系统浏览器设置。', level: 'error');
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('无法打开 ${target.name}（${target.webUrl}）')),
      );
    }
  }

  Future<void> _openProxyAppChooser() async {
    final detected = _lastResult?.detectedProxyApps ?? const <MobileProxyApp>[];
    final choices = detected.isNotEmpty ? detected : knownMobileProxyApps.take(8).toList();
    await showModalBottomSheet<void>(
      context: context,
      backgroundColor: _p.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
      ),
      builder: (ctx) {
        return SafeArea(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(20, 14, 20, 20),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  detected.isNotEmpty ? '打开本机已安装的代理软件' : '快捷唤起常用手机代理软件',
                  style: TextStyle(
                    fontSize: 16,
                    fontWeight: FontWeight.w700,
                    color: _p.ink,
                  ),
                ),
                const SizedBox(height: 10),
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: [
                    for (final app in choices)
                      ActionChip(
                        avatar: const Icon(Icons.vpn_key_rounded, size: 16),
                        label: Text(app.name),
                        onPressed: () async {
                          Navigator.of(ctx).pop();
                          final ok = await widget.bridge.launchApp(
                            androidPackage:
                                app.androidPackage.isNotEmpty ? app.androidPackage : null,
                            iosScheme: app.iosScheme.isNotEmpty ? app.iosScheme : null,
                          );
                          _appendLog(
                            ok ? '已唤起 ${app.name}' : '未在设备上检测到 ${app.name}',
                            level: ok ? 'good' : 'warn',
                          );
                        },
                      ),
                  ],
                ),
              ],
            ),
          ),
        );
      },
    );
  }

  Future<void> _openNodeSwitcher() async {
    final reports = _lastResult?.reports ?? const [];
    final eps = reports
        .map((r) => r.endpoint)
        .whereType<ControllerEndpoint>()
        .toList();
    final ep = eps.isNotEmpty ? eps.first : null;
    if (ep == null) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('当前未检测到 Clash / Mihomo / Stash 控制接口，可在「设置」填写本机或局域网端口。'),
        ),
      );
      return;
    }
    await showNodeSwitcherSheet(
      context: context,
      palette: _p,
      endpoint: ep,
      clashApi: widget.engine.clashApi,
      rollbackService: _rollbackService,
      onSwitched: () {
        if (mounted) setState(() {});
        unawaited(_runCheck());
      },
    );
  }

  Future<void> _openGeminiAclSheet() async {
    var netState = _lastResult?.networkState ?? await widget.bridge.getNetworkState();
    if (netState.platform == 'android' && netState.geminiPackages.isEmpty) {
      final audited = await widget.bridge.inspectPackages(geminiSuiteSpecs);
      if (audited.isNotEmpty) {
        netState = netState.copyWith(geminiPackages: audited);
      }
    }
    if (!mounted) return;
    final aclReport = widget.auditService.analyzeGeminiAccessControl(
      networkState: netState,
      reports: _lastResult?.reports ?? const [],
    );
    await showGeminiAccessControlSheet(
      context: context,
      palette: _p,
      report: aclReport,
      onAutoPatchClash: () async {
        final ep = aclReport.fixableEndpoint;
        if (ep == null) return false;
        final prev = List<String>.from(aclReport.currentClashIncludePackages);
        final merged = <String>{
          ...prev,
          ...aclReport.recommendedPackages,
        }.toList();
        final ok = await widget.engine.clashApi.patchTunIncludePackages(ep, merged);
        if (ok) {
          _rollbackService.recordTunIncludePackageChange(
            endpoint: ep,
            previousPackages: prev,
            newPackages: merged,
          );
          if (mounted) {
            _appendLog(
              '已通过 ${ep.name} 控制接口补全 Gemini 访问控制白名单（支持撤销）。',
              level: 'good',
            );
            unawaited(_runCheck());
          }
        }
        return ok;
      },
      onOpenProxyApp: () => unawaited(_openProxyAppChooser()),
      onOpenGeminiWeb: () async {
        final ok = await widget.bridge.openUrl('https://gemini.google.com/app');
        if (mounted) {
          _appendLog(
            ok ? '已通过浏览器直达 Gemini 网页版' : '无法打开外部浏览器访问 Gemini 网页版',
            level: ok ? 'good' : 'error',
          );
        }
      },
    );
  }

  Future<void> _openRollbackSheet() async {
    await showMobileRollbackSheet(
      context: context,
      palette: _p,
      rollbackService: _rollbackService,
      clashApi: widget.engine.clashApi,
      relayRunning: widget.relay.running,
      relayPort: widget.relay.port,
      onStopRelay: () async {
        await widget.relay.stop();
        if (mounted) {
          setState(() => _socksBridgeActive = false);
        }
        if (widget.settings.relayEnabled) {
          widget.onSettingsChanged(widget.settings.copyWith(relayEnabled: false));
        }
        _appendLog('已停止统一本地中继。', level: 'good');
      },
      onResetSettings: () async {
        _controllerSecret = '';
        await widget.relay.stop();
        if (mounted) {
          setState(() => _socksBridgeActive = false);
        }
        widget.onSettingsChanged(
          AppSettings(
            darkMode: widget.settings.darkMode,
            guideDismissed: true,
          ),
        );
        _appendLog('已恢复软件默认设置并清除内存密钥。', level: 'good');
      },
      onChanged: () {
        if (mounted) {
          setState(() {});
          unawaited(_runCheck());
        }
      },
    );
  }

  Future<void> _openAuditSheet() async {
    final netState = _lastResult?.networkState ?? await widget.bridge.getNetworkState();
    final reports = _lastResult?.reports ?? const <ProxyReport>[];
    final findings = _lastFindings.isNotEmpty
        ? _lastFindings
        : await widget.auditService.diagnoseNetworkHealth(
            networkState: netState,
            reports: reports,
          );
    if (!mounted) return;
    final reportText = widget.auditService.buildDiagnosticReport(
      networkState: netState,
      reports: reports,
      detectedProxyApps: _lastResult?.detectedProxyApps ?? const [],
      findings: findings,
      proxyMode: widget.settings.proxyMode,
      relayPort: widget.relay.running ? widget.relay.port : null,
    );
    await showNetworkAuditSheet(
      context: context,
      palette: _p,
      findings: findings,
      diagnosticReport: reportText,
      onOpenGeminiAcl: netState.platform == 'android' ? () => unawaited(_openGeminiAclSheet()) : null,
      onOpenRollback: () => unawaited(_openRollbackSheet()),
    );
  }

  @override
  Widget build(BuildContext context) {
    final reports = _lastResult?.reports ?? const <ProxyReport>[];
    final targets = _allTargets;
    int readyCount = 0;
    for (final t in targets) {
      final best = _bestReportForTarget(t, reports);
      if (best != null &&
          t.hosts.every((h) => best.probes[h]?.passing == true)) {
        readyCount++;
      }
    }
    final ratio = targets.isEmpty ? 0.0 : readyCount / targets.length;
    final Color ringColor;
    final String ringSub;
    if (_checking) {
      ringColor = AppPalette.sky;
      ringSub = '检测中';
    } else if (_lastResult == null) {
      ringColor = AppPalette.sky;
      ringSub = '待检测';
    } else if (readyCount == targets.length && targets.isNotEmpty) {
      ringColor = AppPalette.emerald;
      ringSub = '全部就绪';
    } else if (readyCount > 0) {
      ringColor = AppPalette.amber;
      ringSub = '部分就绪';
    } else {
      ringColor = AppPalette.rose;
      ringSub = '待处理';
    }

    final mergedProbes = <String, (ProbeResult, String)>{};
    for (final rep in reports) {
      final label =
          rep.port > 0 ? '${rep.provider} :${rep.port}' : rep.provider;
      for (final entry in rep.probes.entries) {
        final existing = mergedProbes[entry.key];
        if (existing == null || (entry.value.passing && !existing.$1.passing)) {
          mergedProbes[entry.key] = (entry.value, label);
        }
      }
    }

    return Scaffold(
      backgroundColor: _p.canvas,
      body: RefreshIndicator(
        color: AppPalette.teal,
        onRefresh: _runCheck,
        child: CustomScrollView(
          slivers: [
            SliverToBoxAdapter(
              child: _buildHeroHeader(
                readyCount: readyCount,
                totalCount: targets.length,
                ratio: ratio,
                ringColor: ringColor,
                ringSub: ringSub,
                reports: reports,
              ),
            ),
            SliverToBoxAdapter(child: _buildQuickActions(reports)),
            SliverToBoxAdapter(child: _buildAccessControlBanner(reports)),
            SliverToBoxAdapter(child: _buildCategoryBar()),
            SliverPadding(
              padding: const EdgeInsets.symmetric(horizontal: 14),
              sliver: SliverLayoutBuilder(
                builder: (context, constraints) {
                  final cols = constraints.crossAxisExtent >= 680 ? 2 : 1;
                  final items = _filteredTargets;
                  if (cols == 1) {
                    return SliverList.separated(
                      itemCount: items.length,
                      separatorBuilder: (_, __) => const SizedBox(height: 10),
                      itemBuilder: (_, i) => _buildTargetCard(items[i], reports),
                    );
                  }
                  return SliverGrid.builder(
                    itemCount: items.length,
                    gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
                      crossAxisCount: 2,
                      crossAxisSpacing: 10,
                      mainAxisSpacing: 10,
                      mainAxisExtent: 124,
                    ),
                    itemBuilder: (_, i) => _buildTargetCard(items[i], reports),
                  );
                },
              ),
            ),
            SliverToBoxAdapter(child: _buildProbePanel(mergedProbes)),
            const SliverToBoxAdapter(child: SizedBox(height: 28)),
          ],
        ),
      ),
    );
  }

  Widget _buildHeroHeader({
    required int readyCount,
    required int totalCount,
    required double ratio,
    required Color ringColor,
    required String ringSub,
    required List<ProxyReport> reports,
  }) {
    final netState = _lastResult?.networkState;
    final relayActive =
        (widget.settings.relayEnabled || _socksBridgeActive) &&
            widget.relay.running &&
            widget.relay.port != null;

    return Container(
      color: _p.heroBg,
      child: SafeArea(
        bottom: false,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                crossAxisAlignment: CrossAxisAlignment.center,
                children: [
                  _HealthRing(
                    ratio: ratio,
                    color: ringColor,
                    primary: _lastResult == null ? '—' : '$readyCount/$totalCount',
                    subtitle: ringSub,
                  ),
                  const SizedBox(width: 14),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(
                          children: [
                            Expanded(
                              child: Text(
                                'AI 代理启动器',
                                style: TextStyle(
                                  fontSize: 19,
                                  fontWeight: FontWeight.w800,
                                  color: _p.heroText,
                                ),
                              ),
                            ),
                            _heroIconBtn(
                              icon: Icons.history_rounded,
                              tooltip: _rollbackService.hasChanges
                                  ? '撤销修改 (${_rollbackService.count})'
                                  : '撤销修改',
                              highlight: _rollbackService.hasChanges,
                              onTap: _openRollbackSheet,
                            ),
                            const SizedBox(width: 6),
                            _heroIconBtn(
                              icon: Icons.help_outline_rounded,
                              tooltip: '使用说明',
                              onTap: _openGuide,
                            ),
                            const SizedBox(width: 6),
                            _heroIconBtn(
                              icon: widget.settings.darkMode
                                  ? Icons.light_mode_rounded
                                  : Icons.dark_mode_rounded,
                              tooltip: '切换主题',
                              onTap: () => widget.onSettingsChanged(
                                widget.settings
                                    .copyWith(darkMode: !widget.settings.darkMode),
                              ),
                            ),
                          ],
                        ),
                        const SizedBox(height: 6),
                        Wrap(
                          spacing: 6,
                          runSpacing: 6,
                          children: [
                            _heroBadge(
                              label: netState == null
                                  ? '移动端全协议'
                                  : (netState.vpnActive
                                      ? '${netState.platform.toUpperCase()} VPN 已激活'
                                      : '${netState.platform.toUpperCase()} · 无本机 VPN'),
                              dotColor: (netState?.vpnActive ?? false)
                                  ? AppPalette.emerald
                                  : AppPalette.sky,
                            ),
                            GestureDetector(
                              onTap: _toggleRelay,
                              child: _heroBadge(
                                label: relayActive
                                    ? (widget.settings.relayEnabled
                                        ? '⇄ 中继 :${widget.relay.port}'
                                        : '⇄ SOCKS5桥 :${widget.relay.port}')
                                    : '⇄ 统一中继',
                                dotColor: relayActive
                                    ? AppPalette.emerald
                                    : _p.muted,
                                highlight: relayActive,
                              ),
                            ),
                          ],
                        ),
                      ],
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 12),
              if (reports.isEmpty)
                _heroBadge(
                  label: _checking
                      ? '正在探测本机代理、SOCKS5 与透明路由出口…'
                      : (_lastResult == null
                          ? '点击下方「一键全检」开始验证'
                          : '未发现可用代理（点击「设置」可连局域网代理）'),
                  dotColor: _checking
                      ? AppPalette.sky
                      : (_lastResult == null ? AppPalette.sky : AppPalette.rose),
                )
              else
                SizedBox(
                  height: 32,
                  child: ListView.separated(
                    scrollDirection: Axis.horizontal,
                    itemCount: reports.length,
                    separatorBuilder: (_, __) => const SizedBox(width: 8),
                    itemBuilder: (_, i) => _buildProxyPill(reports[i]),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _heroIconBtn({
    required IconData icon,
    required String tooltip,
    required VoidCallback onTap,
    bool highlight = false,
  }) {
    return Tooltip(
      message: tooltip,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(10),
        child: Container(
          padding: const EdgeInsets.all(6),
          decoration: BoxDecoration(
            color: highlight ? const Color(0xFF451A03) : _p.heroCard,
            borderRadius: BorderRadius.circular(10),
            border: Border.all(
              color: highlight ? AppPalette.amber : _p.heroBorder,
            ),
          ),
          child: Icon(
            icon,
            size: 17,
            color: highlight ? const Color(0xFFFDE68A) : _p.heroText,
          ),
        ),
      ),
    );
  }

  Widget _heroBadge({
    required String label,
    required Color dotColor,
    bool highlight = false,
  }) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
      decoration: BoxDecoration(
        color: highlight ? const Color(0xFF0F3430) : _p.heroCard,
        borderRadius: BorderRadius.circular(20),
        border: Border.all(
          color: highlight ? AppPalette.teal : _p.heroBorder,
        ),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            width: 7,
            height: 7,
            decoration: BoxDecoration(color: dotColor, shape: BoxShape.circle),
          ),
          const SizedBox(width: 6),
          Text(
            label,
            style: TextStyle(
              fontSize: 11,
              fontWeight: FontWeight.w600,
              color: highlight ? const Color(0xFF5EEAD4) : _p.heroText,
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildProxyPill(ProxyReport report) {
    final restricted = isRestrictedRegion(report.region);
    final Color dot;
    if (report.issue.isNotEmpty) {
      dot = AppPalette.rose;
    } else if (restricted) {
      dot = AppPalette.amber;
    } else if (report.passingCount == report.probes.length &&
        report.probes.isNotEmpty) {
      dot = AppPalette.emerald;
    } else if (report.hasHealthyProbe) {
      dot = AppPalette.amber;
    } else {
      dot = AppPalette.rose;
    }

    final String badge;
    final Color badgeBg;
    final Color badgeFg;
    if (report.scheme == 'socks5') {
      badge = 'SOCKS5';
      badgeBg = const Color(0xFFFDE68A);
      badgeFg = const Color(0xFF78350F);
    } else if (report.tun == true) {
      badge = 'VPN';
      badgeBg = const Color(0xFF6EE7B7);
      badgeFg = const Color(0xFF064E3B);
    } else if (report.endpoint != null) {
      badge = 'API';
      badgeBg = const Color(0xFFC7D2FE);
      badgeFg = const Color(0xFF312E81);
    } else {
      badge = 'HTTP';
      badgeBg = const Color(0xFFBAE6FD);
      badgeFg = const Color(0xFF0C4A6E);
    }

    final reg = regionLabel(report.region);
    final meta = [
      if (reg.isNotEmpty) (restricted ? '$reg·受限' : reg),
      if (report.latencyMs != null) '${report.latencyMs}ms',
    ].join(' · ');

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(
        color: _p.heroCard,
        borderRadius: BorderRadius.circular(20),
        border: Border.all(
          color: restricted ? AppPalette.amber : _p.heroBorder,
        ),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            width: 7,
            height: 7,
            decoration: BoxDecoration(color: dot, shape: BoxShape.circle),
          ),
          const SizedBox(width: 6),
          Text(
            report.provider,
            style: TextStyle(
              fontSize: 12,
              fontWeight: FontWeight.w700,
              color: _p.heroText,
            ),
          ),
          if (report.port > 0) ...[
            const SizedBox(width: 4),
            Text(
              ':${report.port}',
              style: const TextStyle(fontSize: 11, color: AppPalette.sky),
            ),
          ],
          if (meta.isNotEmpty) ...[
            const SizedBox(width: 6),
            Text(
              meta,
              style: TextStyle(
                fontSize: 11,
                color: restricted ? const Color(0xFFFBBF24) : const Color(0xFF94A3B8),
              ),
            ),
          ],
          const SizedBox(width: 6),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
            decoration: BoxDecoration(
              color: badgeBg,
              borderRadius: BorderRadius.circular(6),
            ),
            child: Text(
              badge,
              style: TextStyle(
                fontSize: 9,
                fontWeight: FontWeight.w800,
                color: badgeFg,
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildQuickActions(List<ProxyReport> reports) {
    final hasApi = reports.any((r) => r.endpoint != null);
    return Padding(
      padding: const EdgeInsets.fromLTRB(14, 12, 14, 4),
      child: Column(
        children: [
          Row(
            children: [
              Expanded(
                flex: 3,
                child: FilledButton.icon(
                  style: FilledButton.styleFrom(
                    backgroundColor: AppPalette.teal,
                    foregroundColor: Colors.white,
                    padding: const EdgeInsets.symmetric(vertical: 12),
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(14),
                    ),
                  ),
                  onPressed: _checking ? null : _runCheck,
                  icon: _checking
                      ? const SizedBox(
                          width: 16,
                          height: 16,
                          child: CircularProgressIndicator(
                            strokeWidth: 2,
                            color: Colors.white,
                          ),
                        )
                      : const Icon(Icons.radar_rounded, size: 18),
                  label: Text(
                    _checking ? '正在全检…' : '一键全检',
                    style: const TextStyle(fontWeight: FontWeight.w700),
                  ),
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                flex: 2,
                child: OutlinedButton.icon(
                  style: OutlinedButton.styleFrom(
                    padding: const EdgeInsets.symmetric(vertical: 12),
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(14),
                    ),
                  ),
                  onPressed: _openNodeSwitcher,
                  icon: Icon(
                    Icons.swap_horiz_rounded,
                    size: 18,
                    color: hasApi ? AppPalette.teal : _p.muted,
                  ),
                  label: const Text('切节点'),
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Row(
            children: [
              Expanded(
                child: _miniToolBtn(
                  icon: Icons.health_and_safety_outlined,
                  label: '网络体检',
                  onTap: _openAuditSheet,
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: _miniToolBtn(
                  icon: Icons.tune_rounded,
                  label: '控制接口',
                  onTap: () => showControllerConfigSheet(
                    context: context,
                    palette: _p,
                    currentSettings: widget.settings,
                    currentSecret: _controllerSecret,
                    onSave: (updated, secret) {
                      _controllerSecret = secret;
                      widget.onSettingsChanged(updated);
                      unawaited(_runCheck());
                    },
                  ),
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: _miniToolBtn(
                  icon: Icons.vpn_lock_rounded,
                  label: '代理软件',
                  onTap: _openProxyAppChooser,
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _miniToolBtn({
    required IconData icon,
    required String label,
    required VoidCallback onTap,
  }) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(12),
      child: Container(
        padding: const EdgeInsets.symmetric(vertical: 9),
        decoration: BoxDecoration(
          color: _p.surface,
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: _p.border),
        ),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(icon, size: 15, color: AppPalette.teal),
            const SizedBox(width: 5),
            Text(
              label,
              style: TextStyle(
                fontSize: 12,
                fontWeight: FontWeight.w600,
                color: _p.ink,
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildAccessControlBanner(List<ProxyReport> reports) {
    final netState = _lastResult?.networkState;
    if (netState == null || netState.platform != 'android') {
      return const SizedBox.shrink();
    }
    final acl = widget.auditService.analyzeGeminiAccessControl(
      networkState: netState,
      reports: reports,
    );
    if (acl.severity != 'error') {
      return const SizedBox.shrink();
    }
    return Padding(
      padding: const EdgeInsets.fromLTRB(14, 8, 14, 0),
      child: InkWell(
        borderRadius: BorderRadius.circular(14),
        onTap: _openGeminiAclSheet,
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
          decoration: BoxDecoration(
            color: _p.amberPale,
            borderRadius: BorderRadius.circular(14),
            border: Border.all(color: AppPalette.amber),
          ),
          child: Row(
            children: [
              Icon(Icons.shield_outlined, size: 18, color: _p.amberInk),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  '${acl.headline} · 点此查看 Gemini 访问控制修复方案',
                  style: TextStyle(
                    fontSize: 12,
                    fontWeight: FontWeight.w600,
                    color: _p.amberInk,
                  ),
                ),
              ),
              Icon(Icons.chevron_right_rounded, size: 18, color: _p.amberInk),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildCategoryBar() {
    final cats = [
      ('all', '全部 (${_allTargets.length})'),
      ('app', '手机 App'),
      ('web', '网页服务'),
      ('api', 'API 接口'),
    ];
    return Padding(
      padding: const EdgeInsets.fromLTRB(14, 10, 14, 10),
      child: Row(
        children: [
          Expanded(
            child: SizedBox(
              height: 34,
              child: ListView.separated(
                scrollDirection: Axis.horizontal,
                itemCount: cats.length,
                separatorBuilder: (_, __) => const SizedBox(width: 6),
                itemBuilder: (_, i) {
                  final (key, label) = cats[i];
                  final active = _categoryFilter == key;
                  return ChoiceChip(
                    label: Text(label, style: const TextStyle(fontSize: 12)),
                    selected: active,
                    selectedColor: _p.tealPale,
                    showCheckmark: false,
                    onSelected: (_) => setState(() => _categoryFilter = key),
                  );
                },
              ),
            ),
          ),
          const SizedBox(width: 6),
          InkWell(
            onTap: () => showManageTargetsSheet(
              context: context,
              palette: _p,
              currentSettings: widget.settings,
              onUpdate: (updated) {
                widget.onSettingsChanged(updated);
                setState(() {});
              },
            ),
            borderRadius: BorderRadius.circular(10),
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 7),
              decoration: BoxDecoration(
                color: _p.surface,
                borderRadius: BorderRadius.circular(10),
                border: Border.all(color: _p.border),
              ),
              child: Text(
                '+ 管理',
                style: TextStyle(
                  fontSize: 12,
                  fontWeight: FontWeight.w600,
                  color: _p.ink,
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildTargetCard(AiTarget target, List<ProxyReport> reports) {
    final best = _bestReportForTarget(target, reports);
    final probes = best == null
        ? const <ProbeResult>[]
        : target.hosts
            .map((h) => best.probes[h])
            .whereType<ProbeResult>()
            .toList();
    final allPass = probes.length == target.hosts.length &&
        probes.isNotEmpty &&
        probes.every((p) => p.passing);
    final anyPass = probes.any((p) => p.passing);
    final restricted = best != null && isRestrictedRegion(best.region);
    final installed = _lastResult?.installedAiIds.contains(target.id) == true;

    String statusText;
    Color statusBg;
    Color statusFg;
    Color dotColor;
    if (_checking) {
      statusText = '检测中';
      statusBg = _p.surfaceAlt;
      statusFg = _p.muted;
      dotColor = AppPalette.sky;
    } else if (_lastResult == null) {
      statusText = '待检测';
      statusBg = _p.surfaceAlt;
      statusFg = _p.muted;
      dotColor = _p.muted;
    } else if (allPass && restricted) {
      statusText = '地区受限';
      statusBg = _p.amberPale;
      statusFg = _p.amberInk;
      dotColor = AppPalette.amber;
    } else if (allPass) {
      statusText = '检测通过';
      statusBg = _p.tealPale;
      statusFg = _p.tealInk;
      dotColor = AppPalette.emerald;
    } else if (anyPass) {
      statusText = '部分通过';
      statusBg = _p.amberPale;
      statusFg = _p.amberInk;
      dotColor = AppPalette.amber;
    } else {
      statusText = '待处理';
      statusBg = _p.rosePale;
      statusFg = _p.roseInk;
      dotColor = AppPalette.rose;
    }

    String routeLine = target.description;
    if (best != null) {
      final relayPrefix =
          ((widget.settings.relayEnabled || best.scheme == 'socks5') &&
                  widget.relay.running &&
                  widget.relay.port != null &&
                  best.port > 0)
              ? '中继 :${widget.relay.port} → '
              : '';
      final portPart = best.port > 0 ? ' :${best.port}' : '';
      final firstNode = probes.map((p) => p.node).firstWhere(
            (n) => n.isNotEmpty,
            orElse: () => '',
          );
      final nodePart = firstNode.isNotEmpty ? ' · $firstNode' : '';
      routeLine = '线路 · $relayPrefix${best.provider}$portPart$nodePart';
    }

    return Container(
      padding: const EdgeInsets.all(13),
      decoration: BoxDecoration(
        color: _p.surface,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(
          color: allPass && !restricted ? AppPalette.teal.withAlpha(140) : _p.border,
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Row(
            children: [
              Stack(
                clipBehavior: Clip.none,
                children: [
                  ClipRRect(
                    borderRadius: BorderRadius.circular(10),
                    child: target.iconAsset.isNotEmpty
                        ? Image.asset(
                            target.iconAsset,
                            width: 38,
                            height: 38,
                            fit: BoxFit.cover,
                            errorBuilder: (_, __, ___) => _fallbackIcon(),
                          )
                        : _fallbackIcon(),
                  ),
                  Positioned(
                    right: -2,
                    bottom: -2,
                    child: Container(
                      width: 11,
                      height: 11,
                      decoration: BoxDecoration(
                        color: dotColor,
                        shape: BoxShape.circle,
                        border: Border.all(color: _p.surface, width: 2),
                      ),
                    ),
                  ),
                ],
              ),
              const SizedBox(width: 11),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Flexible(
                          child: Text(
                            target.name,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: TextStyle(
                              fontSize: 15,
                              fontWeight: FontWeight.w700,
                              color: _p.ink,
                            ),
                          ),
                        ),
                        if (installed) ...[
                          const SizedBox(width: 6),
                          Container(
                            padding: const EdgeInsets.symmetric(
                              horizontal: 6,
                              vertical: 2,
                            ),
                            decoration: BoxDecoration(
                              color: _p.tealPale,
                              borderRadius: BorderRadius.circular(6),
                            ),
                            child: Text(
                              '已安装',
                              style: TextStyle(
                                fontSize: 10,
                                fontWeight: FontWeight.w700,
                                color: _p.tealInk,
                              ),
                            ),
                          ),
                        ],
                      ],
                    ),
                    const SizedBox(height: 2),
                    Text(
                      routeLine,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(fontSize: 11.5, color: _p.muted),
                    ),
                  ],
                ),
              ),
            ],
          ),
          const SizedBox(height: 10),
          Row(
            children: [
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 4),
                decoration: BoxDecoration(
                  color: statusBg,
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Text(
                  statusText,
                  style: TextStyle(
                    fontSize: 11,
                    fontWeight: FontWeight.w700,
                    color: statusFg,
                  ),
                ),
              ),
              const Spacer(),
              if (target.id == 'gemini_app' &&
                  (_lastResult?.networkState.platform == 'android' ||
                      (_lastResult == null && Platform.isAndroid))) ...[
                OutlinedButton(
                  style: OutlinedButton.styleFrom(
                    padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 6),
                    minimumSize: Size.zero,
                    tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                    side: const BorderSide(color: AppPalette.amber),
                    foregroundColor: _p.amberInk,
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(10),
                    ),
                  ),
                  onPressed: _openGeminiAclSheet,
                  child: const Text(
                    '访问控制修复',
                    style: TextStyle(fontSize: 11, fontWeight: FontWeight.w700),
                  ),
                ),
                const SizedBox(width: 6),
              ],
              FilledButton.tonalIcon(
                style: FilledButton.styleFrom(
                  backgroundColor: AppPalette.teal,
                  foregroundColor: Colors.white,
                  padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                  minimumSize: Size.zero,
                  tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(10),
                  ),
                ),
                onPressed: () => _openTarget(target),
                icon: const Icon(Icons.open_in_new_rounded, size: 14),
                label: Text(
                  installed ? '唤起 App' : '打开',
                  style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w700),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _fallbackIcon() {
    return Container(
      width: 38,
      height: 38,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: _p.tealPale,
        borderRadius: BorderRadius.circular(10),
      ),
      child: const Icon(Icons.public_rounded, color: AppPalette.teal, size: 22),
    );
  }

  Widget _buildProbePanel(Map<String, (ProbeResult, String)> mergedProbes) {
    final defaultHosts = <String>{
      for (final t in _allTargets) ...t.hosts,
    }.toList();

    return Container(
      margin: const EdgeInsets.fromLTRB(14, 16, 14, 0),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: _p.surface,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: _p.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Text(
                '节点与服务探针状态',
                style: TextStyle(
                  fontSize: 14,
                  fontWeight: FontWeight.w700,
                  color: _p.ink,
                ),
              ),
              const Spacer(),
              TextButton(
                onPressed: () => setState(() => _logExpanded = !_logExpanded),
                child: Text(
                  _logExpanded ? '收起日志' : '展开日志 (${_logs.length})',
                  style: const TextStyle(fontSize: 12),
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Wrap(
            spacing: 6,
            runSpacing: 6,
            children: [
              for (final host in defaultHosts) _buildProbeChip(host, mergedProbes[host]),
            ],
          ),
          if (_logExpanded) ...[
            const SizedBox(height: 12),
            Container(
              width: double.infinity,
              constraints: const BoxConstraints(maxHeight: 220),
              padding: const EdgeInsets.all(10),
              decoration: BoxDecoration(
                color: _p.surfaceAlt,
                borderRadius: BorderRadius.circular(12),
                border: Border.all(color: _p.border),
              ),
              child: _logs.isEmpty
                  ? Text('暂无运行日志', style: TextStyle(fontSize: 12, color: _p.muted))
                  : ListView.builder(
                      shrinkWrap: true,
                      itemCount: _logs.length,
                      itemBuilder: (_, i) {
                        final (msg, lvl) = _logs[i];
                        final color = lvl == 'good'
                            ? AppPalette.emerald
                            : (lvl == 'error'
                                ? AppPalette.rose
                                : (lvl == 'warn' ? AppPalette.amber : _p.muted));
                        return Padding(
                          padding: const EdgeInsets.only(bottom: 4),
                          child: Text(
                            msg,
                            style: TextStyle(fontSize: 11.5, color: color, height: 1.35),
                          ),
                        );
                      },
                    ),
            ),
          ],
        ],
      ),
    );
  }

  Widget _buildProbeChip(String host, (ProbeResult, String)? info) {
    final label = probeShortLabels[host] ?? host.split('/').first;
    final probe = info?.$1;
    final provider = info?.$2 ?? '';
    Color dot;
    Color bg;
    Color fg;
    if (probe == null) {
      dot = _p.muted;
      bg = _p.surfaceAlt;
      fg = _p.muted;
    } else if (probe.passing) {
      dot = AppPalette.emerald;
      bg = _p.tealPale;
      fg = _p.tealInk;
    } else {
      dot = AppPalette.rose;
      bg = _p.rosePale;
      fg = _p.roseInk;
    }

    return InkWell(
      borderRadius: BorderRadius.circular(20),
      onTap: () {
        final detail = probe == null
            ? '$host · 待检测'
            : '$host\n线路：$provider · ${routeLabels[probe.route] ?? probe.route}'
                '${probe.node.isNotEmpty ? " · 节点 ${probe.node}" : ""}'
                '${probe.region.isNotEmpty ? " · 出口 ${regionLabel(probe.region)}(${probe.region})" : ""}'
                '${probe.latencyMs != null ? " · ${probe.latencyMs}ms" : ""}\n'
                '详情：${probe.detail}';
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(detail), duration: const Duration(seconds: 4)),
        );
      },
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 5),
        decoration: BoxDecoration(
          color: bg,
          borderRadius: BorderRadius.circular(20),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              width: 6,
              height: 6,
              decoration: BoxDecoration(color: dot, shape: BoxShape.circle),
            ),
            const SizedBox(width: 5),
            Text(
              label,
              style: TextStyle(fontSize: 11, fontWeight: FontWeight.w600, color: fg),
            ),
          ],
        ),
      ),
    );
  }
}

class _HealthRing extends StatelessWidget {
  const _HealthRing({
    required this.ratio,
    required this.color,
    required this.primary,
    required this.subtitle,
  });

  final double ratio;
  final Color color;
  final String primary;
  final String subtitle;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: 76,
      height: 76,
      child: CustomPaint(
        painter: _RingPainter(ratio: ratio.clamp(0.0, 1.0), color: color),
        child: Center(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(
                primary,
                style: const TextStyle(
                  color: Color(0xFFF8FAFC),
                  fontSize: 15,
                  fontWeight: FontWeight.w800,
                ),
              ),
              Text(
                subtitle,
                style: const TextStyle(
                  color: Color(0xFF94A3B8),
                  fontSize: 9.5,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _RingPainter extends CustomPainter {
  const _RingPainter({required this.ratio, required this.color});

  final double ratio;
  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    final center = Offset(size.width / 2, size.height / 2);
    final radius = min(size.width, size.height) / 2 - 5;
    final bgPaint = Paint()
      ..color = const Color(0xFF1E293B)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 6;
    canvas.drawCircle(center, radius, bgPaint);

    if (ratio > 0) {
      final fgPaint = Paint()
        ..color = color
        ..style = PaintingStyle.stroke
        ..strokeCap = StrokeCap.round
        ..strokeWidth = 6;
      canvas.drawArc(
        Rect.fromCircle(center: center, radius: radius),
        -pi / 2,
        2 * pi * ratio,
        false,
        fgPaint,
      );
    }
  }

  @override
  bool shouldRepaint(_RingPainter oldDelegate) =>
      oldDelegate.ratio != ratio || oldDelegate.color != color;
}
