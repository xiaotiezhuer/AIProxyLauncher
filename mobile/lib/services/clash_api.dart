import 'dart:async';
import 'dart:convert';
import 'dart:io';

import '../models/models.dart';
import 'region_utils.dart';

const Set<String> _clashConfigKeys = {
  'mode',
  'port',
  'mixed-port',
  'socks-port',
  'redir-port',
  'tproxy-port',
  'allow-lan',
  'tun',
};

const Set<String> _directChainTokens = {'DIRECT', 'direct', 'REJECT', 'reject'};

class ControllerProbeResult {
  const ControllerProbeResult({this.endpoint, this.needsSecret = false});

  final ControllerEndpoint? endpoint;
  final bool needsSecret;
}

class ClashApiClient {
  const ClashApiClient({this.defaultTimeout = const Duration(seconds: 3)});

  final Duration defaultTimeout;

  Future<(int, String)> _request(
    ControllerEndpoint endpoint,
    String method,
    String path, {
    Map<String, dynamic>? body,
    Duration? timeout,
  }) async {
    final client = HttpClient()
      ..connectionTimeout = timeout ?? defaultTimeout
      ..findProxy = (_) => 'DIRECT';
    try {
      final uri = Uri.parse('http://${endpoint.host}:${endpoint.port}$path');
      final req = await client.openUrl(method, uri).timeout(timeout ?? defaultTimeout);
      req.headers.set(HttpHeaders.hostHeader, 'localhost');
      if (endpoint.secret.isNotEmpty) {
        req.headers.set(HttpHeaders.authorizationHeader, 'Bearer ${endpoint.secret}');
      }
      if (body != null) {
        final encoded = utf8.encode(jsonEncode(body));
        req.headers.contentType = ContentType.json;
        req.contentLength = encoded.length;
        req.add(encoded);
      }
      final resp = await req.close().timeout(timeout ?? defaultTimeout);
      final text = await resp
          .transform(utf8.decoder)
          .join()
          .timeout(timeout ?? defaultTimeout);
      return (resp.statusCode, text);
    } finally {
      client.close(force: true);
    }
  }

  Future<ControllerProbeResult> probeController(
    String host,
    int port, {
    String secret = '',
    String name = 'Clash API',
    Duration timeout = const Duration(milliseconds: 900),
  }) async {
    final ep = ControllerEndpoint(host: host, port: port, secret: secret, name: name);
    try {
      final (status, body) = await _request(ep, 'GET', '/configs', timeout: timeout);
      if (status == 401) {
        final low = body.toLowerCase();
        if (low.contains('unauthorized') || low.contains('message')) {
          return const ControllerProbeResult(needsSecret: true);
        }
        return const ControllerProbeResult();
      }
      if (status != 200) return const ControllerProbeResult();
      final decoded = jsonDecode(body);
      if (decoded is Map<String, dynamic> &&
          decoded.keys.any(_clashConfigKeys.contains)) {
        return ControllerProbeResult(endpoint: ep);
      }
    } catch (_) {
      // Not a Clash/Mihomo controller
    }
    return const ControllerProbeResult();
  }

  Future<Map<String, dynamic>?> getConfigs(ControllerEndpoint endpoint) async {
    try {
      final (status, body) = await _request(endpoint, 'GET', '/configs');
      if (status != 200) return null;
      final decoded = jsonDecode(body);
      return decoded is Map<String, dynamic> ? decoded : null;
    } catch (_) {
      return null;
    }
  }

  Future<Map<String, dynamic>?> getConnections(ControllerEndpoint endpoint) async {
    try {
      final (status, body) = await _request(endpoint, 'GET', '/connections');
      if (status != 200) return null;
      final decoded = jsonDecode(body);
      return decoded is Map<String, dynamic> ? decoded : null;
    } catch (_) {
      return null;
    }
  }

  (String, String) observedRouteDetail(
    Map<String, dynamic>? snapshot,
    String targetHost,
  ) {
    if (snapshot == null) return ('unknown', '');
    final rows = snapshot['connections'];
    if (rows is! List) return ('unknown', '');
    final want = targetHost.split('/').first.toLowerCase();
    for (final raw in rows.reversed) {
      if (raw is! Map) continue;
      final meta = raw['metadata'];
      if (meta is! Map) continue;
      final host = (meta['host'] ?? meta['sniffHost'] ?? '')
          .toString()
          .toLowerCase()
          .replaceAll(RegExp(r'\.$'), '');
      if (host != want) continue;
      final chains = (raw['chains'] as List<dynamic>? ?? const [])
          .map((e) => e.toString().trim())
          .where((e) => e.isNotEmpty)
          .toList();
      if (chains.isEmpty) continue;
      final outbound = chains.first;
      if (_directChainTokens.contains(outbound)) {
        return ('direct', outbound);
      }
      return ('proxy', outbound);
    }
    return ('unknown', '');
  }

  Future<List<SelectorGroup>> listSelectorGroups(ControllerEndpoint endpoint) async {
    try {
      final (status, body) = await _request(endpoint, 'GET', '/proxies');
      if (status != 200) return const [];
      final decoded = jsonDecode(body);
      if (decoded is! Map<String, dynamic>) return const [];
      final proxies = decoded['proxies'];
      if (proxies is! Map<String, dynamic>) return const [];

      final groups = <SelectorGroup>[];
      for (final entry in proxies.entries) {
        final val = entry.value;
        if (val is! Map<String, dynamic>) continue;
        final type = (val['type'] ?? '').toString();
        if (type != 'Selector' && type != 'URLTest' && type != 'Fallback') {
          continue;
        }
        final all = (val['all'] as List<dynamic>? ?? const [])
            .map((e) => e.toString())
            .toList();
        if (all.isEmpty) continue;
        final current = (val['now'] ?? '').toString();
        final delays = <String, int?>{};
        for (final node in all) {
          final nodeObj = proxies[node];
          int? delay;
          if (nodeObj is Map<String, dynamic>) {
            final hist = nodeObj['history'];
            if (hist is List && hist.isNotEmpty) {
              final last = hist.last;
              if (last is Map && last['delay'] is int) {
                final d = last['delay'] as int;
                delay = d > 0 ? d : null;
              }
            }
          }
          delays[node] = delay;
        }
        groups.add(SelectorGroup(
          name: entry.key,
          current: current,
          nodes: all,
          delays: delays,
        ));
      }
      groups.sort((a, b) {
        final aScore = a.name.toLowerCase().contains('proxy') ||
                a.name.contains('节点') ||
                a.name.contains('选择') ||
                a.name.contains('AI')
            ? 0
            : 1;
        final bScore = b.name.toLowerCase().contains('proxy') ||
                b.name.contains('节点') ||
                b.name.contains('选择') ||
                b.name.contains('AI')
            ? 0
            : 1;
        if (aScore != bScore) return aScore.compareTo(bScore);
        return a.name.compareTo(b.name);
      });
      return groups;
    } catch (_) {
      return const [];
    }
  }

  Future<int?> testNodeDelay(
    ControllerEndpoint endpoint,
    String nodeName, {
    String testUrl = 'https://www.gstatic.com/generate_204',
    int timeoutMs = 4000,
  }) async {
    try {
      final encoded = Uri.encodeComponent(nodeName);
      final encodedUrl = Uri.encodeQueryComponent(testUrl);
      final (status, body) = await _request(
        endpoint,
        'GET',
        '/proxies/$encoded/delay?timeout=$timeoutMs&url=$encodedUrl',
        timeout: Duration(milliseconds: timeoutMs + 1000),
      );
      if (status != 200) return null;
      final decoded = jsonDecode(body);
      if (decoded is Map<String, dynamic> && decoded['delay'] is int) {
        final d = decoded['delay'] as int;
        return d > 0 ? d : null;
      }
    } catch (_) {}
    return null;
  }

  Future<bool> switchNode(
    ControllerEndpoint endpoint,
    String groupName,
    String nodeName,
  ) async {
    try {
      final encoded = Uri.encodeComponent(groupName);
      final (status, _) = await _request(
        endpoint,
        'PUT',
        '/proxies/$encoded',
        body: {'name': nodeName},
      );
      return status == 204 || status == 200;
    } catch (_) {
      return false;
    }
  }

  Future<bool> setTunEnabled(ControllerEndpoint endpoint, bool enabled) async {
    try {
      final (status, _) = await _request(
        endpoint,
        'PATCH',
        '/configs',
        body: {
          'tun': {'enable': enabled},
        },
      );
      return status == 204 || status == 200;
    } catch (_) {
      return false;
    }
  }

  /// Extracts a string list field (e.g. `include-package` or `exclude-package`) from `/configs` `tun`.
  static List<String> extractTunStringList(
    Map<String, dynamic>? cfg,
    String key,
  ) {
    if (cfg == null) return const [];
    final tun = cfg['tun'];
    if (tun is! Map) return const [];
    final raw = tun[key];
    if (raw is! List) return const [];
    return raw
        .map((e) => e.toString().trim())
        .where((e) => e.isNotEmpty)
        .toSet()
        .toList();
  }

  /// Patches Clash/Mihomo `tun.include-package` via controller API (supports one-click Gemini whitelist fix & rollback).
  Future<bool> patchTunIncludePackages(
    ControllerEndpoint endpoint,
    List<String> packages,
  ) async {
    try {
      final cleaned = packages
          .map((e) => e.trim())
          .where((e) => e.isNotEmpty)
          .toSet()
          .toList();
      final (status, _) = await _request(
        endpoint,
        'PATCH',
        '/configs',
        body: {
          'tun': {
            'enable': true,
            'include-package': cleaned,
          },
        },
      );
      return status == 204 || status == 200;
    } catch (_) {
      return false;
    }
  }

  /// Auto-pick the fastest non-restricted AI node inside the primary Selector group.
  Future<(String, String, int)?> autoPickBestNonRestrictedNode(
    ControllerEndpoint endpoint, {
    String? preferredGroup,
  }) async {
    final groups = await listSelectorGroups(endpoint);
    if (groups.isEmpty) return null;
    final group = preferredGroup != null
        ? groups.firstWhere(
            (g) => g.name == preferredGroup,
            orElse: () => groups.first,
          )
        : groups.first;
    final candidates = group.nodes
        .where((n) => !nodeLooksRestricted(n))
        .take(12)
        .toList();
    if (candidates.isEmpty) return null;

    final results = await Future.wait(
      candidates.map((n) async => (n, await testNodeDelay(endpoint, n))),
    );
    final alive = results.where((e) => e.$2 != null).toList()
      ..sort((a, b) => a.$2!.compareTo(b.$2!));
    if (alive.isEmpty) return null;
    final winner = alive.first;
    final switched = await switchNode(endpoint, group.name, winner.$1);
    if (!switched) return null;
    return (group.name, winner.$1, winner.$2!);
  }
}

