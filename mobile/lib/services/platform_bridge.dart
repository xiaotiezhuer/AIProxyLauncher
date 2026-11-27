import 'dart:io';
import 'package:flutter/services.dart';
import '../models/models.dart';

abstract class PlatformBridge {
  Future<String?> getAppDataDir();
  Future<NativeNetworkState> getNetworkState();
  Future<Set<String>> getInstalledApps({
    required List<String> androidPackages,
    required List<String> iosSchemes,
  });
  Future<List<AndroidPackageAudit>> inspectPackages(
    List<GeminiPackageSpec> specs,
  );
  Future<bool> launchApp({
    String? androidPackage,
    List<String> fallbackAndroidPackages = const [],
    String? iosScheme,
    String? url,
  });
  Future<bool> openUrl(String url);
}

class MethodChannelPlatformBridge implements PlatformBridge {
  MethodChannelPlatformBridge({MethodChannel? channel})
      : _channel = channel ?? const MethodChannel('com.aiproxylauncher/native');

  final MethodChannel _channel;

  @override
  Future<String?> getAppDataDir() async {
    try {
      final dir = await _channel.invokeMethod<String>('getAppDataDir');
      if (dir != null && dir.isNotEmpty) return dir;
    } catch (_) {
      // Fallback for unit/widget tests running without native host
    }
    return null;
  }

  @override
  Future<NativeNetworkState> getNetworkState() async {
    try {
      final raw = await _channel.invokeMethod<dynamic>('getNetworkState');
      if (raw is Map) {
        final ifaces = (raw['vpnInterfaces'] as List<dynamic>? ?? const [])
            .map((e) => e.toString())
            .toList();
        return NativeNetworkState(
          platform: (raw['platform'] ?? _dartPlatformName()).toString(),
          osVersion: (raw['osVersion'] ?? Platform.operatingSystemVersion)
              .toString(),
          vpnActive: raw['vpnActive'] == true,
          activeNetworkIsVpn: raw['activeNetworkIsVpn'] == true,
          vpnBypassedForSelf: raw['vpnBypassedForSelf'] == true,
          selfPackageName: (raw['selfPackageName'] ??
                  'com.aiproxylauncher.ai_proxy_launcher')
              .toString(),
          vpnInterfaces: ifaces,
          httpProxyHost: raw['httpProxyHost']?.toString(),
          httpProxyPort: raw['httpProxyPort'] is int
              ? raw['httpProxyPort'] as int
              : int.tryParse(raw['httpProxyPort']?.toString() ?? ''),
          socksProxyHost: raw['socksProxyHost']?.toString(),
          socksProxyPort: raw['socksProxyPort'] is int
              ? raw['socksProxyPort'] as int
              : int.tryParse(raw['socksProxyPort']?.toString() ?? ''),
        );
      }
    } catch (_) {
      // Fallback to dart:io NetworkInterface inspection
    }
    return _fallbackNetworkState();
  }

  Future<NativeNetworkState> _fallbackNetworkState() async {
    final vpnIfaces = <String>[];
    try {
      final list = await NetworkInterface.list(includeLoopback: false);
      for (final iface in list) {
        final low = iface.name.toLowerCase();
        if (low.startsWith('tun') ||
            low.startsWith('utun') ||
            low.startsWith('wg') ||
            low.startsWith('ppp')) {
          vpnIfaces.add(iface.name);
        }
      }
    } catch (_) {}
    return NativeNetworkState(
      platform: _dartPlatformName(),
      osVersion: Platform.operatingSystemVersion,
      vpnActive: vpnIfaces.isNotEmpty,
      vpnInterfaces: vpnIfaces,
    );
  }

  @override
  Future<Set<String>> getInstalledApps({
    required List<String> androidPackages,
    required List<String> iosSchemes,
  }) async {
    try {
      final raw = await _channel.invokeMethod<dynamic>('getInstalledApps', {
        'packages': androidPackages,
        'schemes': iosSchemes,
      });
      if (raw is List) {
        return raw.map((e) => e.toString()).toSet();
      }
    } catch (_) {}
    return <String>{};
  }

  @override
  Future<List<AndroidPackageAudit>> inspectPackages(
    List<GeminiPackageSpec> specs,
  ) async {
    if (!Platform.isAndroid || specs.isEmpty) {
      return const [];
    }
    final specByPkg = {for (final s in specs) s.packageName: s};
    try {
      final raw = await _channel.invokeMethod<dynamic>('inspectPackages', {
        'packages': specs.map((s) => s.packageName).toList(),
      });
      if (raw is List) {
        final result = <AndroidPackageAudit>[];
        for (final item in raw) {
          if (item is! Map) continue;
          final pkg = (item['packageName'] ?? '').toString();
          final spec = specByPkg[pkg];
          if (spec == null) continue;
          final rawName = (item['appName'] ?? '').toString().trim();
          result.add(AndroidPackageAudit(
            packageName: pkg,
            displayName: rawName.isNotEmpty ? rawName : spec.fallbackName,
            role: spec.role,
            installed: item['installed'] == true,
            isSystemApp: item['isSystemApp'] == true,
            hasInternetPermission: item['hasInternetPermission'] == true,
            hasLauncherActivity: item['hasLauncherActivity'] == true,
            essential: spec.essential,
          ));
        }
        return result;
      }
    } catch (_) {}
    return const [];
  }

  @override
  Future<bool> launchApp({
    String? androidPackage,
    List<String> fallbackAndroidPackages = const [],
    String? iosScheme,
    String? url,
  }) async {
    try {
      final ok = await _channel.invokeMethod<bool>('launchApp', {
        'packageName': androidPackage,
        'fallbackPackages': fallbackAndroidPackages,
        'scheme': iosScheme,
        'url': url,
      });
      return ok == true;
    } catch (_) {
      return false;
    }
  }

  @override
  Future<bool> openUrl(String url) async {
    try {
      final ok = await _channel.invokeMethod<bool>('openUrl', {'url': url});
      return ok == true;
    } catch (_) {
      return false;
    }
  }

  static String _dartPlatformName() {
    if (Platform.isAndroid) return 'android';
    if (Platform.isIOS) return 'ios';
    return Platform.operatingSystem;
  }
}

