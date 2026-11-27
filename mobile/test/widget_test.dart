import 'dart:io';

import 'package:ai_proxy_launcher/main.dart';
import 'package:ai_proxy_launcher/models/models.dart';
import 'package:ai_proxy_launcher/services/platform_bridge.dart';
import 'package:ai_proxy_launcher/services/proxy_engine.dart';
import 'package:ai_proxy_launcher/services/settings_store.dart';
import 'package:flutter_test/flutter_test.dart';

class _TestBridge implements PlatformBridge {
  @override
  Future<String?> getAppDataDir() async => null;

  @override
  Future<Set<String>> getInstalledApps({
    required List<String> androidPackages,
    required List<String> iosSchemes,
  }) async =>
      {'com.openai.chatgpt', 'com.follow.clash'};

  @override
  Future<List<AndroidPackageAudit>> inspectPackages(
    List<GeminiPackageSpec> specs,
  ) async =>
      const [];

  @override
  Future<NativeNetworkState> getNetworkState() async =>
      const NativeNetworkState(
        platform: 'android',
        osVersion: 'Android 15',
        vpnActive: true,
        activeNetworkIsVpn: true,
        vpnInterfaces: ['tun0'],
      );

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
  testWidgets('HomeScreen renders hero header, AI cards, and settings persistence',
      (WidgetTester tester) async {
    final bridge = _TestBridge();
    late SettingsStore store;
    late AppSettings loaded;
    Directory? tmpDir;
    await tester.runAsync(() async {
      tmpDir = await Directory.systemTemp.createTemp('ai_proxy_mobile_test');
      store = SettingsStore(bridge, overrideDir: tmpDir);
      await store.save(const AppSettings(darkMode: true, guideDismissed: true));
      loaded = await store.load();
    });
    addTearDown(() async {
      if (tmpDir != null && tmpDir!.existsSync()) {
        tmpDir!.deleteSync(recursive: true);
      }
    });
    expect(loaded.guideDismissed, isTrue);

    final engine = MobileProxyEngine(
      bridge: bridge,
      regionProbeOverride: ({
        required traceHost,
        proxyHost,
        proxyPort,
        scheme = 'http',
        timeout = const Duration(seconds: 3),
      }) async =>
          ('US', 95),
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
        detail: 'TLS 已连接',
        route: 'tun_route',
        latencyMs: 95,
        region: 'US',
      ),
    );

    await tester.pumpWidget(
      AiProxyLauncherApp(
        bridgeOverride: bridge,
        engineOverride: engine,
        settingsStoreOverride: store,
        initialSettings: loaded,
        autoRunOnInit: false,
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('AI 代理启动器'), findsOneWidget);
    expect(find.text('一键全检'), findsOneWidget);
    expect(find.text('ChatGPT App'), findsOneWidget);
    expect(find.byTooltip('使用说明'), findsOneWidget);
    expect(find.byTooltip('切换主题'), findsOneWidget);

    // Toggle theme and verify rebuild succeeds cleanly
    await tester.tap(find.byTooltip('切换主题'));
    await tester.pumpAndSettle();
    expect(find.text('AI 代理启动器'), findsOneWidget);
  });
}

