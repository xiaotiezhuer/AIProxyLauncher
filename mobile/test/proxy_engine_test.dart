import 'dart:convert';
import 'dart:io';

import 'package:ai_proxy_launcher/models/models.dart';
import 'package:ai_proxy_launcher/services/clash_api.dart';
import 'package:ai_proxy_launcher/services/platform_bridge.dart';
import 'package:ai_proxy_launcher/services/proxy_engine.dart';
import 'package:ai_proxy_launcher/services/region_utils.dart';
import 'package:flutter_test/flutter_test.dart';

class FakeBridge implements PlatformBridge {
  FakeBridge({
    this.netState = NativeNetworkState.empty,
    this.installed = const {},
    this.inspected = const [],
  });

  final NativeNetworkState netState;
  final Set<String> installed;
  final List<AndroidPackageAudit> inspected;

  @override
  Future<String?> getAppDataDir() async => null;

  @override
  Future<Set<String>> getInstalledApps({
    required List<String> androidPackages,
    required List<String> iosSchemes,
  }) async =>
      installed;

  @override
  Future<List<AndroidPackageAudit>> inspectPackages(
    List<GeminiPackageSpec> specs,
  ) async =>
      inspected;

  @override
  Future<NativeNetworkState> getNetworkState() async => netState;

  @override
  Future<bool> launchApp({
    String? androidPackage,
    List<String> fallbackAndroidPackages = const [],
    String? iosScheme,
    String? url,
  }) async =>
      true;

  @override
  Future<bool> openUrl(String url) async => true;
}

void main() {
  test('Region inference and restricted region filtering', () {
    expect(isRestrictedRegion('HK'), isTrue);
    expect(isRestrictedRegion('CN'), isTrue);
    expect(isRestrictedRegion('US'), isFalse);
    expect(regionLabel('JP'), '日本');
    expect(inferRegionFromNode('🇺🇸 美国高速 01'), 'US');
    expect(inferRegionFromNode('🇭🇰 香港 IPLC'), 'HK');
    expect(nodeLooksRestricted('香港 01'), isTrue);
    expect(nodeLooksRestricted('东京 JP-02'), isFalse);
    expect(parseCloudflareTraceRegion('fl=123\nloc=TW\ntls=TLSv1.3\n'), 'TW');
  });

  test('ClashApiClient probes controller, lists groups, tests delay, avoids HK node',
      () async {
    String selectedNode = '🇭🇰 香港 01';
    String? lastAuth;
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    server.listen((req) async {
      lastAuth = req.headers.value(HttpHeaders.authorizationHeader);
      if (lastAuth != 'Bearer top-secret') {
        req.response
          ..statusCode = 401
          ..write('{"message":"Unauthorized"}');
        await req.response.close();
        return;
      }
      final path = req.uri.path;
      if (path == '/configs') {
        req.response
          ..statusCode = 200
          ..write(jsonEncode({
            'mode': 'rule',
            'mixed-port': 7890,
            'tun': {'enable': true},
          }));
      } else if (path == '/proxies' && req.method == 'GET') {
        req.response
          ..statusCode = 200
          ..write(jsonEncode({
            'proxies': {
              'PROXY': {
                'type': 'Selector',
                'now': selectedNode,
                'all': ['🇭🇰 香港 01', '🇯🇵 日本 01', '🇺🇸 美国 01'],
              },
              '🇭🇰 香港 01': {
                'type': 'Shadowsocks',
                'history': [
                  {'delay': 30}
                ],
              },
              '🇯🇵 日本 01': {
                'type': 'Shadowsocks',
                'history': [
                  {'delay': 120}
                ],
              },
              '🇺🇸 美国 01': {
                'type': 'Shadowsocks',
                'history': [
                  {'delay': 180}
                ],
              },
            },
          }));
      } else if (path.startsWith('/proxies/') && path.endsWith('/delay')) {
        final node = Uri.decodeComponent(
          path.substring('/proxies/'.length, path.length - '/delay'.length),
        );
        final delay = node.contains('日本') ? 85 : 175;
        req.response
          ..statusCode = 200
          ..write(jsonEncode({'delay': delay}));
      } else if (path == '/proxies/PROXY' && req.method == 'PUT') {
        final body = jsonDecode(await utf8.decoder.bind(req).join())
            as Map<String, dynamic>;
        selectedNode = body['name'].toString();
        req.response.statusCode = 204;
      } else {
        req.response.statusCode = 404;
      }
      await req.response.close();
    });

    try {
      const api = ClashApiClient();
      final noAuth = await api.probeController('127.0.0.1', server.port);
      expect(noAuth.endpoint, isNull);
      expect(noAuth.needsSecret, isTrue);

      final withAuth = await api.probeController(
        '127.0.0.1',
        server.port,
        secret: 'top-secret',
        name: 'Stash',
      );
      expect(withAuth.endpoint, isNotNull);

      final picked = await api.autoPickBestNonRestrictedNode(withAuth.endpoint!);
      expect(picked, isNotNull);
      expect(picked!.$2, '🇯🇵 日本 01');
      expect(selectedNode, '🇯🇵 日本 01');
    } finally {
      await server.close(force: true);
    }
  });

  test('MobileProxyEngine transparent router fallback accepts non-CN and rejects CN',
      () async {
    final target = defaultMobileTargets.first;

    // 1. Non-CN transparent router (no VPN, no local ports) -> succeeds
    final routerEngine = MobileProxyEngine(
      bridge: FakeBridge(
        netState: const NativeNetworkState(
          platform: 'ios',
          osVersion: 'iOS 18.0',
          vpnActive: false,
        ),
      ),
      controllerPortsOverride: const [],
      proxyPortsOverride: const [],
      regionProbeOverride: ({
        required traceHost,
        proxyHost,
        proxyPort,
        scheme = 'http',
        timeout = const Duration(seconds: 3),
      }) async =>
          ('US', 78),
      serviceProbeOverride: ({
        required service,
        proxyHost,
        proxyPort,
        scheme = 'http',
        endpoint,
        allowUncontrolled = false,
        timeout = const Duration(seconds: 7),
      }) async =>
          ProbeResult(
        host: service,
        connected: true,
        detail: '网站有响应（HTTP 200）',
        route: 'tun_route',
        latencyMs: 82,
        region: 'US',
      ),
    );

    final okRes = await routerEngine.discoverAndCheck(
      targets: [target],
      settings: const AppSettings(),
    );
    expect(okRes.reports.length, 1);
    expect(okRes.reports.first.provider, '透明代理 / 旁路由');
    expect(okRes.reports.first.region, 'US');
    expect(okRes.reports.first.port, 0);

    // 2. Domestic CN direct -> zero false positives
    final cnEngine = MobileProxyEngine(
      bridge: FakeBridge(),
      controllerPortsOverride: const [],
      proxyPortsOverride: const [],
      regionProbeOverride: ({
        required traceHost,
        proxyHost,
        proxyPort,
        scheme = 'http',
        timeout = const Duration(seconds: 3),
      }) async =>
          ('CN', 18),
    );
    final cnRes = await cnEngine.discoverAndCheck(
      targets: [target],
      settings: const AppSettings(),
    );
    expect(cnRes.reports, isEmpty);
  });

  test('MobileProxyEngine classifies pure SOCKS5 port accurately', () async {
    final socksServer = await ServerSocket.bind(InternetAddress.loopbackIPv4, 0);
    final sub = socksServer.listen((client) {
      client.listen((data) {
        if (data.isNotEmpty && data[0] == 0x05) {
          client.add(const [0x05, 0x00]);
        }
        client.flush().then((_) => client.close());
      });
    });

    try {
      final engine = MobileProxyEngine(bridge: FakeBridge());
      final kind = await engine.classifyLocalPort('127.0.0.1', socksServer.port);
      expect(kind, PortKind.socks5);
    } finally {
      await sub.cancel();
      await socksServer.close();
    }
  });
}

