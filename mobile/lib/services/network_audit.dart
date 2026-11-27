import 'dart:io';

import '../models/models.dart';

typedef DnsResolverFn = Future<Map<String, List<String>>> Function(List<String> hosts);
typedef Ipv6DetectorFn = Future<String> Function();

class GeminiAccessControlReport {
  const GeminiAccessControlReport({
    required this.isAndroid,
    required this.vpnActive,
    required this.vpnBypassedForSelf,
    required this.bardInstalled,
    required this.bardHiddenByNoInternet,
    required this.googleAppInstalled,
    required this.auditedPackages,
    required this.hiddenSystemPackages,
    required this.clashWhitelistRestrictingGemini,
    required this.currentClashIncludePackages,
    required this.missingClashIncludePackages,
    required this.recommendedPackages,
    required this.severity,
    required this.headline,
    required this.explanation,
    this.fixableEndpoint,
  });

  final bool isAndroid;
  final bool vpnActive;
  final bool vpnBypassedForSelf;
  final bool bardInstalled;
  final bool bardHiddenByNoInternet;
  final bool googleAppInstalled;
  final List<AndroidPackageAudit> auditedPackages;
  final List<AndroidPackageAudit> hiddenSystemPackages;
  final bool clashWhitelistRestrictingGemini;
  final ControllerEndpoint? fixableEndpoint;
  final List<String> currentClashIncludePackages;
  final List<String> missingClashIncludePackages;
  final List<String> recommendedPackages;
  final String severity; // "error", "warn", "ok"
  final String headline;
  final String explanation;

  bool get canAutoPatchClash =>
      fixableEndpoint != null && missingClashIncludePackages.isNotEmpty;

  String get copyablePackageList => recommendedPackages.join('\n');

  String get copyableClashYaml {
    final buf = StringBuffer()
      ..writeln('tun:')
      ..writeln('  enable: true')
      ..writeln('  include-package:');
    for (final pkg in recommendedPackages) {
      buf.writeln('    - $pkg');
    }
    return buf.toString().trimRight();
  }
}

class NetworkAuditService {
  const NetworkAuditService();

  /// Analyzes Android Clash-like Access Control (分应用代理/访问控制) pitfalls for Google Gemini.
  GeminiAccessControlReport analyzeGeminiAccessControl({
    required NativeNetworkState networkState,
    List<ProxyReport> reports = const [],
  }) {
    final isAndroid = networkState.platform == 'android';
    final audited = networkState.geminiPackages.isNotEmpty
        ? networkState.geminiPackages
        : [
            for (final spec in geminiSuiteSpecs)
              AndroidPackageAudit(
                packageName: spec.packageName,
                displayName: spec.fallbackName,
                role: spec.role,
                installed: false,
                isSystemApp: spec.packageName == 'com.google.android.gms' ||
                    spec.packageName == 'com.google.android.gsf' ||
                    spec.packageName == 'com.google.android.webview',
                hasInternetPermission:
                    spec.packageName != 'com.google.android.apps.bard',
                essential: spec.essential,
              ),
          ];

    final byPkg = {for (final a in audited) a.packageName: a};
    final bard = byPkg['com.google.android.apps.bard'];
    final google = byPkg['com.google.android.googlequicksearchbox'];
    final bardInstalled = bard?.installed == true;
    final bardHiddenByNoInternet = bard?.hiddenByNoInternetFilter == true;
    final googleAppInstalled = google?.installed == true;
    final hiddenSystem =
        audited.where((a) => a.hiddenBySystemFilter && a.essential).toList();

    final recommended = <String>{
      for (final spec in geminiSuiteSpecs) spec.packageName,
      if (networkState.selfPackageName.isNotEmpty) networkState.selfPackageName,
    }.toList();

    final essentialPkgs = <String>[
      for (final spec in geminiSuiteSpecs)
        if (spec.essential) spec.packageName,
      if (networkState.selfPackageName.isNotEmpty) networkState.selfPackageName,
    ];

    ControllerEndpoint? fixableEp;
    List<String> currentInclude = const [];
    List<String> missingInclude = const [];
    List<String> excludedEssential = const [];
    bool clashRestricting = false;

    for (final rep in reports) {
      if (rep.tunIncludePackages.isNotEmpty) {
        final incSet = rep.tunIncludePackages.toSet();
        final missing = essentialPkgs
            .where((pkg) => !incSet.contains(pkg))
            .toList();
        if (missing.isNotEmpty) {
          clashRestricting = true;
          fixableEp = rep.endpoint;
          currentInclude = rep.tunIncludePackages;
          missingInclude = missing;
          break;
        }
      }
      if (rep.tunExcludePackages.isNotEmpty) {
        final excSet = rep.tunExcludePackages.toSet();
        final excluded = essentialPkgs.where(excSet.contains).toList();
        if (excluded.isNotEmpty) {
          clashRestricting = true;
          excludedEssential = excluded;
          break;
        }
      }
    }

    String severity = 'ok';
    String headline = '未检测到限制 Gemini 的访问控制冲突';
    String explanation =
        '在安卓端，Gemini (com.google.android.apps.bard) 只是入口壳应用，实际联网与账号认证依赖 '
        'Google、Google Play 服务 (gms) 与 Google 服务框架 (gsf)。建议使用黑名单模式或在白名单中完整放行上述关联包。';

    if (isAndroid && clashRestricting) {
      severity = 'error';
      if (missingInclude.isNotEmpty) {
        headline = 'Clash TUN 访问控制白名单缺少 Gemini 依赖包';
        explanation =
            '当前 Clash 配置的 tun.include-package 仅放行了部分应用，缺少 ${missingInclude.join("、")}。'
            '仅放行 Google 会导致 Gemini 登录认证与接口请求绕过代理而失败。';
      } else {
        headline = 'Clash TUN 访问控制黑名单排除了 Gemini 依赖包';
        explanation =
            '当前 Clash 配置的 tun.exclude-package 排除了 ${excludedEssential.join("、")}，'
            '导致 Gemini 登录认证或接口请求绕过代理而失败，请在代理软件访问控制中取消排除。';
      }
    } else if (isAndroid && networkState.vpnBypassedForSelf) {
      severity = 'error';
      headline = '检测到代理软件「访问控制（白名单）」正在拦截未勾选应用';
      explanation =
          '手机已开启 VPN，但本应用未走 VPN，说明 Clash 类软件开启了白名单访问控制。'
          '如果只勾选了「Google」，Gemini 会因缺少 Google Play 服务 (gms/gsf) 而无法走代理；'
          '请在代理软件访问控制中同时勾选显示「系统应用」和「无网络应用」，这样才能看到 Gemini 的 bard 包（com.google.android.apps.bard）并完整放行。';
    } else if (isAndroid &&
        networkState.vpnActive &&
        (bardHiddenByNoInternet || hiddenSystem.isNotEmpty)) {
      severity = 'warn';
      headline = '安卓访问控制默认会隐藏 Gemini (bard) 与 Google 系统组件';
      final reasons = <String>[];
      if (bardHiddenByNoInternet) {
        reasons.add(
          '本机已安装 Gemini（com.google.android.apps.bard）且不含 INTERNET 权限，会被 Clash 访问控制的「过滤无网络权限应用」隐藏',
        );
      }
      if (hiddenSystem.isNotEmpty) {
        reasons.add(
          '${hiddenSystem.map((e) => e.displayName).join("、")} 属于系统应用，默认会被「隐藏系统应用」过滤',
        );
      }
      explanation =
          '${reasons.join("；")}。若访问控制仅勾选「Google」，Gemini 仍无法走代理；建议改为黑名单模式，或在代理软件访问控制中勾选显示「系统应用」和「无网络应用」以显示 Gemini 的 bard 包并补齐全部依赖包。';
    }

    return GeminiAccessControlReport(
      isAndroid: isAndroid,
      vpnActive: networkState.vpnActive,
      vpnBypassedForSelf: networkState.vpnBypassedForSelf,
      bardInstalled: bardInstalled,
      bardHiddenByNoInternet: bardHiddenByNoInternet,
      googleAppInstalled: googleAppInstalled,
      auditedPackages: audited,
      hiddenSystemPackages: hiddenSystem,
      clashWhitelistRestrictingGemini: clashRestricting,
      fixableEndpoint: fixableEp,
      currentClashIncludePackages: currentInclude,
      missingClashIncludePackages: missingInclude,
      recommendedPackages: recommended,
      severity: severity,
      headline: headline,
      explanation: explanation,
    );
  }

  static Future<Map<String, List<String>>> defaultDnsResolver(
    List<String> hosts,
  ) async {
    final result = <String, List<String>>{};
    for (final host in hosts) {
      try {
        final addrs = await InternetAddress.lookup(
          host,
          type: InternetAddressType.IPv4,
        ).timeout(const Duration(seconds: 2));
        result[host] = addrs.map((e) => e.address).toSet().toList();
      } catch (_) {
        result[host] = const [];
      }
    }
    return result;
  }

  static Future<String> defaultGlobalIpv6Detector() async {
    try {
      final ifaces = await NetworkInterface.list(
        includeLoopback: false,
        type: InternetAddressType.IPv6,
      );
      for (final iface in ifaces) {
        for (final addr in iface.addresses) {
          final lower = addr.address.toLowerCase();
          if (lower == '::1' ||
              lower == '::' ||
              lower.startsWith('fe80:') ||
              lower.startsWith('fc') ||
              lower.startsWith('fd')) {
            continue;
          }
          return addr.address;
        }
      }
    } catch (_) {}
    return '';
  }

  Future<List<HealthFinding>> diagnoseNetworkHealth({
    required NativeNetworkState networkState,
    List<ProxyReport> reports = const [],
    DnsResolverFn? dnsResolver,
    Ipv6DetectorFn? ipv6Detector,
  }) async {
    final findings = <HealthFinding>[];
    final resolve = dnsResolver ?? defaultDnsResolver;
    final detectV6 = ipv6Detector ?? defaultGlobalIpv6Detector;

    // 1. Mobile VPN & System Proxy Status
    if (networkState.vpnActive) {
      final ifaceText = networkState.vpnInterfaces.isNotEmpty
          ? '（${networkState.vpnInterfaces.take(2).join(', ')}）'
          : '';
      findings.add(HealthFinding(
        category: 'VPN',
        status: 'ok',
        title: '系统 VPN / 虚拟网卡已激活',
        detail: '当前设备处于 VPN 接管状态$ifaceText，手机原生 AI App 将自动走代理线路。',
      ));
    } else if (networkState.httpProxyPort != null) {
      findings.add(HealthFinding(
        category: 'VPN',
        status: 'warn',
        title: '仅检测到 Wi-Fi 系统代理（未开 VPN）',
        detail:
            '当前配置了 Wi-Fi 代理 ${networkState.httpProxyHost}:${networkState.httpProxyPort}；部分原生 App 可能忽略 Wi-Fi 代理，建议开启代理软件的 VPN 模式。',
      ));
    } else {
      findings.add(const HealthFinding(
        category: 'VPN',
        status: 'warn',
        title: '未检测到本机 VPN 网卡',
        detail: '若未使用路由器 / 旁路由透明代理，请先打开代理 App 并连接非受限地区节点。',
      ));
    }

    // 1.5 Android Clash Access Control & Gemini Dependency Suite Audit
    final geminiAcl = analyzeGeminiAccessControl(
      networkState: networkState,
      reports: reports,
    );
    if (geminiAcl.isAndroid && geminiAcl.severity != 'ok') {
      findings.add(HealthFinding(
        category: '访问控制',
        status: geminiAcl.severity,
        title: geminiAcl.headline,
        detail: geminiAcl.explanation,
      ));
    }

    // 2. DNS Fake-IP / Poisoning Check
    const hosts = ['chatgpt.com', 'claude.ai', 'accounts.google.com'];
    final resolved = await resolve(hosts);
    final allIps = [for (final list in resolved.values) ...list];
    final fakeIps = allIps
        .where((ip) => ip.startsWith('198.18.') || ip.startsWith('198.19.'))
        .toList();
    final bogonIps = allIps
        .where((ip) =>
            ip.startsWith('127.') ||
            ip.startsWith('0.') ||
            ip.startsWith('169.254.'))
        .toList();

    if (bogonIps.isNotEmpty) {
      findings.add(HealthFinding(
        category: 'DNS',
        status: 'error',
        title: '检测到异常 / 环回 DNS 解析',
        detail:
            '部分 AI 域名解析到了异常地址（${bogonIps.toSet().join(', ')}），请检查代理软件 DNS 设置或私有 DNS。',
      ));
    } else if (fakeIps.isNotEmpty) {
      findings.add(HealthFinding(
        category: 'DNS',
        status: 'ok',
        title: 'Fake-IP DNS 接管正常',
        detail: 'AI 域名已由代理 Fake-IP 接管（示例 ${fakeIps.first}），可有效防止移动网络 DNS 污染。',
      ));
    } else if (allIps.isEmpty) {
      findings.add(const HealthFinding(
        category: 'DNS',
        status: 'warn',
        title: '系统 DNS 暂时无法解析 AI 域名',
        detail: '若已开启代理端口可正常使用，原生 App 建议在代理软件开启 Fake-IP 或加密 DNS（DoH）。',
      ));
    } else {
      final firstIps = [
        for (final list in resolved.values)
          if (list.isNotEmpty) list.first,
      ];
      if (firstIps.length >= 3 && firstIps.toSet().length == 1) {
        findings.add(HealthFinding(
          category: 'DNS',
          status: 'error',
          title: 'DNS 疑似存在污染',
          detail:
              '多个不同 AI 域名均解析到同一 IP（${firstIps.first}），请在代理 App 中开启 Fake-IP 或加密 DNS。',
        ));
      } else if (networkState.vpnActive) {
        findings.add(HealthFinding(
          category: 'DNS',
          status: 'warn',
          title: 'VPN 已开但未检测到 Fake-IP',
          detail: '当前解析为真实 IP（如 ${firstIps.first}）；若 App 加载缓慢或超时，建议开启 Fake-IP。',
        ));
      } else {
        findings.add(const HealthFinding(
          category: 'DNS',
          status: 'ok',
          title: '系统 DNS 解析可用',
          detail: '未发现环回或明显污染特征。',
        ));
      }
    }

    // 3. Global IPv6 Leak Check (privacy-masked)
    final globalV6 = await detectV6();
    if (globalV6.isNotEmpty) {
      final segs = globalV6.split(':');
      final masked = '${segs.take(3).join(':')}::…';
      findings.add(HealthFinding(
        category: 'IPv6',
        status: 'warn',
        title: '检测到公网 IPv6（注意潜在泄漏 / 超时）',
        detail:
            '当前移动网络/Wi-Fi 存在公网 IPv6（$masked）。若 AI App 在 VPN 下卡顿或地区漂移，请在代理软件开启 IPv6 接管或关闭 IPv6 路由。',
      ));
    } else {
      findings.add(const HealthFinding(
        category: 'IPv6',
        status: 'ok',
        title: '无公网 IPv6 泄漏风险',
        detail: '当前未检测到公网 IPv6 地址。',
      ));
    }

    return findings;
  }

  /// Generates a sanitized plain-text report safe for sharing (strips secrets & sensitive IPs).
  String buildDiagnosticReport({
    required NativeNetworkState networkState,
    required List<ProxyReport> reports,
    required List<MobileProxyApp> detectedProxyApps,
    required List<HealthFinding> findings,
    required String proxyMode,
    int? relayPort,
  }) {
    final buf = StringBuffer()
      ..writeln('=== AI 代理启动器 (Mobile) · 脱敏诊断报告 ===')
      ..writeln('系统: ${networkState.platform.toUpperCase()} · ${networkState.osVersion}')
      ..writeln(
        'VPN 状态: ${networkState.vpnActive ? "已激活" : "未开启"}'
        '${networkState.vpnInterfaces.isNotEmpty ? " (${networkState.vpnInterfaces.join(", ")})" : ""}',
      )
      ..writeln(
        '代理模式: $proxyMode | 检测到本机代理 App: '
        '${detectedProxyApps.isEmpty ? "无" : detectedProxyApps.map((e) => e.name).join("、")}',
      )
      ..writeln(
        '统一本地中继: ${relayPort != null ? "已开启 127.0.0.1:$relayPort" : "未开启"}',
      )
      ..writeln()
      ..writeln('[活跃代理与服务探针]');

    if (reports.isEmpty) {
      buf.writeln('- 无可用代理');
    } else {
      for (final rep in reports) {
        if (rep.issue.isNotEmpty) {
          buf.writeln('- ${rep.provider}: 错误 - ${rep.issue}');
          continue;
        }
        final portStr = rep.port > 0 ? ' :${rep.port}' : ' (系统路由)';
        final regStr = rep.region.isNotEmpty ? ' ${rep.region}' : '';
        final latStr = rep.latencyMs != null ? ' ${rep.latencyMs}ms' : '';
        buf.writeln(
          '- ${rep.provider}$portStr | scheme=${rep.scheme} | mode=${rep.mode} | '
          'tun=${rep.tun} | api=${rep.endpoint != null}$regStr$latStr',
        );
        for (final pr in rep.probes.values) {
          final node = pr.node.isNotEmpty ? ' | node=${pr.node}' : '';
          final ms = pr.latencyMs != null ? ' | ${pr.latencyMs}ms' : '';
          buf.writeln(
            '    * ${pr.host}: ok=${pr.connected} route=${pr.route}$node$ms (${pr.detail})',
          );
        }
      }
    }

    buf
      ..writeln()
      ..writeln('[网络底层体检 (VPN / 访问控制 / DNS / IPv6)]');
    for (final f in findings) {
      buf.writeln('- [${f.status.toUpperCase()}] ${f.category} - ${f.title}: ${f.detail}');
    }
    if (networkState.platform == 'android') {
      final acl = analyzeGeminiAccessControl(
        networkState: networkState,
        reports: reports,
      );
      buf
        ..writeln()
        ..writeln('[Android 访问控制与 Gemini 依赖包]')
        ..writeln(
          '- VPN 接管本应用: ${networkState.vpnBypassedForSelf ? "否（访问控制白名单已排除本应用）" : (networkState.vpnActive ? "是" : "未开 VPN")}',
        )
        ..writeln(
          '- Gemini (bard) 状态: ${acl.bardInstalled ? (acl.bardHiddenByNoInternet ? "已安装（无 INTERNET 权限，易被访问控制隐藏）" : "已安装") : "未单独安装（依赖 Google 宿主）"}',
        );
      if (acl.missingClashIncludePackages.isNotEmpty) {
        buf.writeln(
          '- Clash TUN include-package 缺失: ${acl.missingClashIncludePackages.join(", ")}',
        );
      }
    }
    return buf.toString().trimRight();
  }
}

