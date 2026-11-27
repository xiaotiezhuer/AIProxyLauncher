import 'dart:convert';
import 'dart:io';

import 'package:ai_proxy_launcher/models/models.dart';
import 'package:ai_proxy_launcher/services/clash_api.dart';
import 'package:ai_proxy_launcher/services/network_audit.dart';
import 'package:ai_proxy_launcher/services/rollback_service.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  const audit = NetworkAuditService();

  test('analyzeGeminiAccessControl detects no-INTERNET bard stub, hidden system gms/gsf, and VPN bypass',
      () async {
    const state = NativeNetworkState(
      platform: 'android',
      osVersion: 'Android 15',
      vpnActive: true,
      activeNetworkIsVpn: false,
      vpnBypassedForSelf: true,
      vpnInterfaces: ['tun0'],
      geminiPackages: [
        AndroidPackageAudit(
          packageName: 'com.google.android.googlequicksearchbox',
          displayName: 'Google',
          role: '宿主',
          installed: true,
          isSystemApp: true,
          hasInternetPermission: true,
          hasLauncherActivity: true,
          essential: true,
        ),
        AndroidPackageAudit(
          packageName: 'com.google.android.apps.bard',
          displayName: 'Gemini',
          role: '壳应用',
          installed: true,
          isSystemApp: false,
          hasInternetPermission: false,
          hasLauncherActivity: true,
          essential: true,
        ),
        AndroidPackageAudit(
          packageName: 'com.google.android.gms',
          displayName: 'Google Play 服务',
          role: '认证',
          installed: true,
          isSystemApp: true,
          hasInternetPermission: true,
          essential: true,
        ),
      ],
    );

    final report = audit.analyzeGeminiAccessControl(networkState: state);
    expect(report.severity, 'error');
    expect(report.bardInstalled, isTrue);
    expect(report.bardHiddenByNoInternet, isTrue);
    expect(report.hiddenSystemPackages.map((e) => e.packageName),
        contains('com.google.android.gms'));
    expect(report.copyablePackageList, contains('com.google.android.apps.bard'));
    expect(report.copyablePackageList, contains('com.google.android.gms'));
    expect(report.copyablePackageList, contains('com.google.android.gsf'));
    expect(report.copyableClashYaml, contains('include-package:'));

    final findings = await audit.diagnoseNetworkHealth(
      networkState: state,
      dnsResolver: (_) async => {
        'chatgpt.com': ['198.18.0.1'],
        'claude.ai': ['198.18.0.2'],
        'accounts.google.com': ['198.18.0.3'],
      },
      ipv6Detector: () async => '',
    );
    final aclFinding = findings.firstWhere((f) => f.category == '访问控制');
    expect(aclFinding.status, 'error');
    expect(aclFinding.detail, contains('bard'));
  });

  test('Clash API tun.include-package only allowing Google is detected, auto-patched, and rolled back',
      () async {
    List<String> includePackages = ['com.google.android.googlequicksearchbox'];
    bool? lastPatchedTunEnable;
    String currentNode = '🇭🇰 香港 01';

    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    server.listen((req) async {
      final path = req.uri.path;
      if (path == '/configs' && req.method == 'PATCH') {
        final body = jsonDecode(await utf8.decoder.bind(req).join())
            as Map<String, dynamic>;
        final tun = body['tun'] as Map<String, dynamic>?;
        if (tun != null && tun['include-package'] is List) {
          lastPatchedTunEnable = tun['enable'] as bool?;
          includePackages = (tun['include-package'] as List)
              .map((e) => e.toString())
              .toList();
        }
        req.response.statusCode = 204;
      } else if (path == '/proxies/PROXY' && req.method == 'PUT') {
        final body = jsonDecode(await utf8.decoder.bind(req).join())
            as Map<String, dynamic>;
        currentNode = body['name'].toString();
        req.response.statusCode = 204;
      } else {
        req.response.statusCode = 404;
      }
      await req.response.close();
    });

    try {
      final ep = ControllerEndpoint(
        host: '127.0.0.1',
        port: server.port,
        name: 'FlClash',
      );
      final proxyRep = ProxyReport(
        provider: 'FlClash',
        mode: 'rule',
        host: '127.0.0.1',
        port: 7890,
        probes: const {},
        tun: true,
        endpoint: ep,
        tunIncludePackages: includePackages,
      );

      final acl = audit.analyzeGeminiAccessControl(
        networkState: const NativeNetworkState(
          platform: 'android',
          osVersion: 'Android 15',
          vpnActive: true,
          activeNetworkIsVpn: true,
        ),
        reports: [proxyRep],
      );
      expect(acl.clashWhitelistRestrictingGemini, isTrue);
      expect(acl.canAutoPatchClash, isTrue);
      expect(acl.missingClashIncludePackages,
          contains('com.google.android.apps.bard'));
      expect(acl.missingClashIncludePackages,
          contains('com.google.android.gms'));

      const api = ClashApiClient();
      final rollback = MobileRollbackService();

      // 1. Patch Clash TUN include-package & record for rollback
      final prevPkgs = List<String>.from(acl.currentClashIncludePackages);
      final merged = <String>{...prevPkgs, ...acl.recommendedPackages}.toList();
      expect(await api.patchTunIncludePackages(ep, merged), isTrue);
      expect(lastPatchedTunEnable, isTrue);
      rollback.recordTunIncludePackageChange(
        endpoint: ep,
        previousPackages: prevPkgs,
        newPackages: merged,
      );
      expect(includePackages, contains('com.google.android.apps.bard'));
      expect(includePackages, contains('com.google.android.gms'));

      // 2. Switch node twice and verify first-write-wins rollback to original node
      expect(await api.switchNode(ep, 'PROXY', '🇯🇵 日本 01'), isTrue);
      rollback.recordNodeSwitch(
        endpoint: ep,
        groupName: 'PROXY',
        previousNode: '🇭🇰 香港 01',
        newNode: '🇯🇵 日本 01',
      );
      expect(await api.switchNode(ep, 'PROXY', '🇺🇸 美国 01'), isTrue);
      rollback.recordNodeSwitch(
        endpoint: ep,
        groupName: 'PROXY',
        previousNode: '🇯🇵 日本 01',
        newNode: '🇺🇸 美国 01',
      );
      expect(rollback.count, 2);

      // 3. Undo all session modifications -> restores original include-package AND original node
      final (done, errors) = await rollback.undoAll(api);
      expect(errors, isEmpty);
      expect(done.length, 2);
      expect(rollback.hasChanges, isFalse);
      expect(includePackages, ['com.google.android.googlequicksearchbox']);
      expect(currentNode, '🇭🇰 香港 01');
    } finally {
      await server.close(force: true);
    }
  });

  test('analyzeGeminiAccessControl reports accurate blacklist error when tun.exclude-package blocks gms',
      () {
    const rep = ProxyReport(
      provider: 'FlClash',
      mode: 'rule',
      host: '127.0.0.1',
      port: 7890,
      probes: {},
      tun: true,
      tunExcludePackages: ['com.google.android.gms'],
    );
    final acl = audit.analyzeGeminiAccessControl(
      networkState: const NativeNetworkState(
        platform: 'android',
        osVersion: 'Android 15',
        vpnActive: true,
        activeNetworkIsVpn: true,
      ),
      reports: const [rep],
    );
    expect(acl.severity, 'error');
    expect(acl.headline, contains('黑名单'));
    expect(acl.explanation, contains('com.google.android.gms'));
    expect(acl.explanation, isNot(contains('缺少 。')));
  });
}
