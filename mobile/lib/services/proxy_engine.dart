import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math';
import 'dart:typed_data';

import '../models/models.dart';
import 'clash_api.dart';
import 'platform_bridge.dart';
import 'region_utils.dart';

const List<int> commonControllerPorts = [9090, 9097, 9091, 9099, 16756, 6171];
const List<int> commonProxyPorts = [
  7890,
  7897,
  7891,
  10809,
  10808,
  1080,
  12334,
  2080,
  6152,
  3067,
  1082,
  8888,
];

enum PortKind { httpProxy, socks5, closed, notProxy }

typedef RegionProbeFn = Future<(String, int?)> Function({
  required String traceHost,
  String? proxyHost,
  int? proxyPort,
  String scheme,
  Duration timeout,
});

typedef ServiceProbeFn = Future<ProbeResult> Function({
  required String service,
  String? proxyHost,
  int? proxyPort,
  String scheme,
  ControllerEndpoint? endpoint,
  bool allowUncontrolled,
  Duration timeout,
});

class _RawTunnel {
  _RawTunnel(this.socket, this.subscription);

  final RawSocket socket;
  final StreamSubscription<RawSocketEvent> subscription;

  Future<void> close() async {
    try {
      await subscription.cancel();
    } catch (_) {}
    try {
      socket.close();
    } catch (_) {}
  }
}

class MobileProxyEngine {
  MobileProxyEngine({
    required this.bridge,
    ClashApiClient? clashApi,
    RegionProbeFn? regionProbeOverride,
    ServiceProbeFn? serviceProbeOverride,
    List<int>? controllerPortsOverride,
    List<int>? proxyPortsOverride,
  })  : clashApi = clashApi ?? const ClashApiClient(),
        _regionProbeOverride = regionProbeOverride,
        _serviceProbeOverride = serviceProbeOverride,
        _controllerPorts = controllerPortsOverride ?? commonControllerPorts,
        _proxyPorts = proxyPortsOverride ?? commonProxyPorts;

  final PlatformBridge bridge;
  final ClashApiClient clashApi;
  final RegionProbeFn? _regionProbeOverride;
  final ServiceProbeFn? _serviceProbeOverride;
  final List<int> _controllerPorts;
  final List<int> _proxyPorts;

  Future<PortKind> classifyLocalPort(
    String host,
    int port, {
    Duration timeout = const Duration(milliseconds: 500),
  }) async {
    Socket? sock;
    try {
      sock = await Socket.connect(host, port, timeout: timeout);
    } catch (_) {
      return PortKind.closed;
    }

    try {
      sock.add(const [0x05, 0x01, 0x00]);
      await sock.flush().timeout(timeout);
      final reply =
          await _readChunk(sock, timeout: const Duration(milliseconds: 350));
      sock.destroy();
      if (reply.length >= 2 &&
          reply[0] == 0x05 &&
          (reply[1] == 0x00 || reply[1] == 0x02 || reply[1] == 0xFF)) {
        if (await _speaksHttpProxy(host, port, timeout: timeout)) {
          return PortKind.httpProxy;
        }
        return PortKind.socks5;
      }
      if (reply.length >= 4 &&
          String.fromCharCodes(reply.take(4)).toUpperCase() == 'HTTP') {
        return PortKind.httpProxy;
      }
    } catch (_) {
      sock.destroy();
    }

    if (await _speaksHttpProxy(host, port, timeout: timeout)) {
      return PortKind.httpProxy;
    }
    return PortKind.notProxy;
  }

  Future<bool> _speaksHttpProxy(
    String host,
    int port, {
    required Duration timeout,
  }) async {
    Socket? s;
    try {
      s = await Socket.connect(host, port, timeout: timeout);
      s.add(ascii.encode(
        'GET http://127.0.0.1/ HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n',
      ));
      await s.flush().timeout(timeout);
      final resp = await _readChunk(s, timeout: timeout);
      if (resp.length >= 5 &&
          String.fromCharCodes(resp.take(5)).toUpperCase() == 'HTTP/') {
        return true;
      }
    } catch (_) {
      return false;
    } finally {
      s?.destroy();
    }
    return false;
  }

  /// Opens a RawSocket tunnel through a local/LAN HTTP CONNECT proxy ready for RawSecureSocket.secure().
  Future<(_RawTunnel?, String)> _openRawHttpTunnel(
    String proxyHost,
    int proxyPort,
    String targetHost,
    Duration timeout,
  ) async {
    RawSocket? raw;
    StreamSubscription<RawSocketEvent>? sub;
    try {
      raw = await RawSocket.connect(proxyHost, proxyPort, timeout: timeout);
      final buf = BytesBuilder(copy: false);
      final ready = Completer<Uint8List>();
      sub = raw.listen(
        (event) {
          if (event == RawSocketEvent.read) {
            final chunk = raw!.read();
            if (chunk != null && chunk.isNotEmpty) {
              buf.add(chunk);
              final bytes = buf.toBytes();
              if (_containsHeaderEnd(bytes) && !ready.isCompleted) {
                ready.complete(bytes);
              } else if (bytes.length > 8192 && !ready.isCompleted) {
                ready.complete(bytes);
              }
            }
          } else if (event == RawSocketEvent.readClosed ||
              event == RawSocketEvent.closed) {
            if (!ready.isCompleted) ready.complete(buf.toBytes());
          }
        },
        onError: (_) {
          if (!ready.isCompleted) ready.complete(Uint8List(0));
        },
        onDone: () {
          if (!ready.isCompleted) ready.complete(buf.toBytes());
        },
      );
      raw.write(ascii.encode(
        'CONNECT $targetHost:443 HTTP/1.1\r\nHost: $targetHost:443\r\nProxy-Connection: Keep-Alive\r\n\r\n',
      ));
      final resp = await ready.future.timeout(timeout);
      final code = _parseHttpStatus(resp);
      if (code == 200) {
        return (_RawTunnel(raw, sub), '');
      }
      await sub.cancel();
      raw.close();
      return (null, '本机代理拒绝建立连接（HTTP ${code ?? "未知"}）');
    } on TimeoutException {
      await sub?.cancel();
      raw?.close();
      return (null, '本机代理响应超时');
    } catch (_) {
      await sub?.cancel();
      raw?.close();
      return (null, '无法通过本机代理建立连接');
    }
  }

  /// Opens a RawSocket tunnel through a local/LAN RFC 1928 SOCKS5 proxy ready for RawSecureSocket.secure().
  Future<(_RawTunnel?, String)> _openRawSocks5Tunnel(
    String proxyHost,
    int proxyPort,
    String targetHost,
    int targetPort,
    Duration timeout,
  ) async {
    RawSocket? raw;
    StreamSubscription<RawSocketEvent>? sub;
    try {
      raw = await RawSocket.connect(proxyHost, proxyPort, timeout: timeout);
      final incoming = <int>[];
      Completer<void>? waiter;

      sub = raw.listen(
        (event) {
          if (event == RawSocketEvent.read) {
            final chunk = raw!.read();
            if (chunk != null && chunk.isNotEmpty) {
              incoming.addAll(chunk);
              if (waiter != null && !waiter!.isCompleted) {
                waiter!.complete();
              }
            }
          } else if (event == RawSocketEvent.readClosed ||
              event == RawSocketEvent.closed) {
            if (waiter != null && !waiter!.isCompleted) {
              waiter!.completeError(const SocketException('SOCKS5 EOF'));
            }
          }
        },
        onError: (Object e) {
          if (waiter != null && !waiter!.isCompleted) {
            waiter!.completeError(e);
          }
        },
        onDone: () {
          if (waiter != null && !waiter!.isCompleted) {
            waiter!.completeError(const SocketException('SOCKS5 closed'));
          }
        },
      );

      Future<List<int>> readExact(int count) async {
        while (incoming.length < count) {
          waiter = Completer<void>();
          await waiter!.future.timeout(timeout);
        }
        final out = incoming.sublist(0, count);
        incoming.removeRange(0, count);
        return out;
      }

      raw.write(const [0x05, 0x01, 0x00]);
      final greeting = await readExact(2);
      if (greeting[0] != 0x05 || greeting[1] != 0x00) {
        throw const SocketException('SOCKS5 handshake rejected');
      }

      final hostBytes = utf8.encode(targetHost);
      final req = <int>[
        0x05,
        0x01,
        0x00,
        0x03,
        hostBytes.length,
        ...hostBytes,
        (targetPort >> 8) & 0xFF,
        targetPort & 0xFF,
      ];
      raw.write(req);

      final reply = await readExact(4);
      if (reply[0] != 0x05 || reply[1] != 0x00) {
        throw SocketException('SOCKS5 connect failed (${reply[1]})');
      }
      final atyp = reply[3];
      if (atyp == 0x01) {
        await readExact(6);
      } else if (atyp == 0x03) {
        final len = (await readExact(1))[0];
        await readExact(len + 2);
      } else if (atyp == 0x04) {
        await readExact(18);
      } else {
        throw const SocketException('Unsupported SOCKS5 ATYP');
      }

      return (_RawTunnel(raw, sub), '');
    } on TimeoutException {
      await sub?.cancel();
      raw?.close();
      return (null, '本机 SOCKS5 代理响应超时');
    } catch (_) {
      await sub?.cancel();
      raw?.close();
      return (null, '无法通过本机 SOCKS5 代理建立连接');
    }
  }

  Future<Uint8List> _rawTlsExchange(
    RawSecureSocket tls,
    String requestText,
    Duration timeout, {
    bool readFullBody = false,
  }) async {
    final buf = BytesBuilder(copy: false);
    final done = Completer<Uint8List>();
    late StreamSubscription<RawSocketEvent> sub;
    sub = tls.listen(
      (event) {
        if (event == RawSocketEvent.read) {
          final chunk = tls.read();
          if (chunk != null && chunk.isNotEmpty) {
            buf.add(chunk);
            final current = buf.toBytes();
            if (!readFullBody && _containsHeaderEnd(current) && !done.isCompleted) {
              done.complete(current);
              sub.cancel();
            } else if (current.length > 32768 && !done.isCompleted) {
              done.complete(current);
              sub.cancel();
            }
          }
        } else if (event == RawSocketEvent.readClosed ||
            event == RawSocketEvent.closed) {
          if (!done.isCompleted) done.complete(buf.toBytes());
        }
      },
      onError: (_) {
        if (!done.isCompleted) done.complete(buf.toBytes());
      },
      onDone: () {
        if (!done.isCompleted) done.complete(buf.toBytes());
      },
    );
    tls.write(ascii.encode(requestText));
    try {
      return await done.future.timeout(timeout);
    } finally {
      await sub.cancel();
    }
  }

  Future<(String, int?)> probeExitRegion({
    required String traceHost,
    String? proxyHost,
    int? proxyPort,
    String scheme = 'http',
    Duration timeout = const Duration(seconds: 3),
  }) async {
    if (_regionProbeOverride != null) {
      return _regionProbeOverride(
        traceHost: traceHost,
        proxyHost: proxyHost,
        proxyPort: proxyPort,
        scheme: scheme,
        timeout: timeout,
      );
    }

    final sw = Stopwatch()..start();
    try {
      if (proxyHost == null || proxyPort == null || proxyPort <= 0) {
        final client = HttpClient()
          ..connectionTimeout = timeout
          ..findProxy = (_) => 'DIRECT';
        try {
          final req = await client
              .getUrl(Uri.parse('https://$traceHost/cdn-cgi/trace'))
              .timeout(timeout);
          req.headers.set(HttpHeaders.userAgentHeader, 'AIProxyLauncher/2.3');
          final resp = await req.close().timeout(timeout);
          final body = await resp.transform(utf8.decoder).join().timeout(timeout);
          sw.stop();
          final reg = parseCloudflareTraceRegion(body);
          return (reg, reg.isNotEmpty ? max(1, sw.elapsedMilliseconds) : null);
        } finally {
          client.close(force: true);
        }
      }

      if (scheme == 'socks5') {
        final (tunnel, _) =
            await _openRawSocks5Tunnel(proxyHost, proxyPort, traceHost, 443, timeout);
        if (tunnel == null) return ('', null);
        RawSecureSocket? tls;
        try {
          tls = await RawSecureSocket.secure(
            tunnel.socket,
            subscription: tunnel.subscription,
            host: traceHost,
          ).timeout(timeout);
          final rawBytes = await _rawTlsExchange(
            tls,
            'GET /cdn-cgi/trace HTTP/1.1\r\nHost: $traceHost\r\nUser-Agent: AIProxyLauncher/2.3\r\nConnection: close\r\n\r\n',
            timeout,
            readFullBody: true,
          );
          sw.stop();
          final body = utf8.decode(rawBytes, allowMalformed: true);
          final reg = parseCloudflareTraceRegion(body);
          return (reg, reg.isNotEmpty ? max(1, sw.elapsedMilliseconds) : null);
        } finally {
          tls?.close();
          if (tls == null) await tunnel.close();
        }
      }

      final client = HttpClient()
        ..connectionTimeout = timeout
        ..findProxy = (_) => 'PROXY $proxyHost:$proxyPort';
      try {
        final req = await client
            .getUrl(Uri.parse('https://$traceHost/cdn-cgi/trace'))
            .timeout(timeout);
        req.headers.set(HttpHeaders.userAgentHeader, 'AIProxyLauncher/2.3');
        final resp = await req.close().timeout(timeout);
        final body = await resp.transform(utf8.decoder).join().timeout(timeout);
        sw.stop();
        final reg = parseCloudflareTraceRegion(body);
        return (reg, reg.isNotEmpty ? max(1, sw.elapsedMilliseconds) : null);
      } finally {
        client.close(force: true);
      }
    } catch (_) {
      return ('', null);
    }
  }

  Future<ProbeResult> probeService({
    required String service,
    String? proxyHost,
    int? proxyPort,
    String scheme = 'http',
    ControllerEndpoint? endpoint,
    bool allowUncontrolled = false,
    Duration timeout = const Duration(seconds: 7),
  }) async {
    if (_serviceProbeOverride != null) {
      return _serviceProbeOverride(
        service: service,
        proxyHost: proxyHost,
        proxyPort: proxyPort,
        scheme: scheme,
        endpoint: endpoint,
        allowUncontrolled: allowUncontrolled,
        timeout: timeout,
      );
    }

    final first = await _connectProbeOnce(
      service: service,
      proxyHost: proxyHost,
      proxyPort: proxyPort,
      scheme: scheme,
      endpoint: endpoint,
      allowUncontrolled: allowUncontrolled,
      timeout: timeout,
    );
    if (!first.connected &&
        first.route != 'direct' &&
        (first.detail == '端口有响应，服务连接超时' ||
            first.detail == '端口有响应，服务连接中断')) {
      return _connectProbeOnce(
        service: service,
        proxyHost: proxyHost,
        proxyPort: proxyPort,
        scheme: scheme,
        endpoint: endpoint,
        allowUncontrolled: allowUncontrolled,
        timeout: timeout,
      );
    }
    return first;
  }

  Future<ProbeResult> _connectProbeOnce({
    required String service,
    String? proxyHost,
    int? proxyPort,
    required String scheme,
    ControllerEndpoint? endpoint,
    required bool allowUncontrolled,
    required Duration timeout,
  }) async {
    final sw = Stopwatch()..start();
    final slash = service.indexOf('/');
    final host = slash >= 0 ? service.substring(0, slash) : service;
    final path = slash >= 0 ? service.substring(slash + 1) : '';

    _RawTunnel? tunnel;
    RawSecureSocket? tls;
    try {
      if (proxyHost == null || proxyPort == null || proxyPort <= 0) {
        try {
          tls = await RawSecureSocket.connect(host, 443, timeout: timeout);
        } on HandshakeException {
          return ProbeResult(host: service, connected: false, detail: '服务证书校验失败');
        } catch (_) {
          return ProbeResult(host: service, connected: false, detail: '系统路由连接失败');
        }
      } else {
        final (opened, err) = scheme == 'socks5'
            ? await _openRawSocks5Tunnel(proxyHost, proxyPort, host, 443, timeout)
            : await _openRawHttpTunnel(proxyHost, proxyPort, host, timeout);
        if (opened == null) {
          return ProbeResult(host: service, connected: false, detail: err);
        }
        tunnel = opened;
        try {
          tls = await RawSecureSocket.secure(
            tunnel.socket,
            subscription: tunnel.subscription,
            host: host,
          ).timeout(timeout);
        } on HandshakeException {
          return ProbeResult(host: service, connected: false, detail: '服务证书校验失败');
        } on TimeoutException {
          return ProbeResult(host: service, connected: false, detail: '端口有响应，服务连接超时');
        } catch (_) {
          return ProbeResult(host: service, connected: false, detail: '端口有响应，服务连接中断');
        }
      }

      var route = 'unknown';
      var node = '';
      if (endpoint != null) {
        final snap = await clashApi.getConnections(endpoint);
        final (r, n) = clashApi.observedRouteDetail(snap, host);
        route = r;
        node = n;
      }
      if (route == 'unknown' && allowUncontrolled) {
        if (proxyHost == null || proxyPort == null || proxyPort <= 0) {
          route = 'tun_route';
        } else if (scheme == 'socks5') {
          route = 'socks5_proxy';
        } else {
          route = 'http_proxy';
        }
      }
      final inferredRegion = node.isNotEmpty ? inferRegionFromNode(node) : '';

      final needsHttpCheck = const {
            'claude.ai',
            'chatgpt.com',
            'gemini.google.com',
            'antigravity.google.com',
            'grok.com',
            'www.perplexity.ai',
          }.contains(host) ||
          service == oauthTokenEndpoint ||
          service == dailyCodeAssistEndpoint ||
          service == codexResponsesEndpoint;

      if (!needsHttpCheck) {
        sw.stop();
        return ProbeResult(
          host: service,
          connected: true,
          detail: 'TLS 已连接',
          route: route,
          latencyMs: max(1, sw.elapsedMilliseconds),
          node: node,
          region: inferredRegion,
        );
      }

      String method = 'HEAD';
      String extraHeaders = 'Connection: close\r\n';
      if (service == codexResponsesEndpoint) {
        method = 'GET';
        final rnd = Random.secure();
        final key = base64Encode(List<int>.generate(16, (_) => rnd.nextInt(256)));
        extraHeaders =
            'Connection: Upgrade\r\nUpgrade: websocket\r\nSec-WebSocket-Version: 13\r\n'
            'Sec-WebSocket-Key: $key\r\nOrigin: https://chatgpt.com\r\n';
      } else if (service == oauthTokenEndpoint ||
          service == dailyCodeAssistEndpoint) {
        method = 'POST';
        extraHeaders =
            'Connection: close\r\nContent-Type: application/x-www-form-urlencoded\r\nContent-Length: 0\r\n';
      }
      final reqText =
          '$method /$path HTTP/1.1\r\nHost: $host\r\nUser-Agent: AIProxyLauncher/2.3\r\n$extraHeaders\r\n';
      final rawResp = await _rawTlsExchange(tls, reqText, timeout);
      sw.stop();
      final statusCode = _parseHttpStatus(rawResp);
      if (statusCode == null) {
        return ProbeResult(
          host: service,
          connected: false,
          detail: '端口有响应，服务连接中断',
          route: route,
          node: node,
          region: inferredRegion,
        );
      }
      final ms = max(1, sw.elapsedMilliseconds);
      if (service == codexResponsesEndpoint) {
        final ok = statusCode == 101 || statusCode == 401;
        return ProbeResult(
          host: service,
          connected: ok,
          detail: ok
              ? 'Codex 服务有响应（HTTP $statusCode）'
              : 'Codex 握手异常（HTTP $statusCode）',
          route: route,
          latencyMs: ms,
          node: node,
          region: inferredRegion,
        );
      }
      if (service == oauthTokenEndpoint || service == dailyCodeAssistEndpoint) {
        final ok = service == oauthTokenEndpoint
            ? (statusCode == 400 || statusCode == 401)
            : statusCode == 401;
        return ProbeResult(
          host: service,
          connected: ok,
          detail: 'Google 接口有响应（HTTP $statusCode）',
          route: route,
          latencyMs: ms,
          node: node,
          region: inferredRegion,
        );
      }
      if (statusCode == 429 || statusCode == 451 || statusCode >= 500) {
        return ProbeResult(
          host: service,
          connected: false,
          detail: '网站返回 HTTP $statusCode',
          route: route,
          latencyMs: ms,
          node: node,
          region: inferredRegion,
        );
      }
      final suffix = statusCode == 403 ? '，可能需要登录或验证' : '';
      return ProbeResult(
        host: service,
        connected: true,
        detail: '网站有响应（HTTP $statusCode）$suffix',
        route: route,
        latencyMs: ms,
        node: node,
        region: inferredRegion,
      );
    } on TimeoutException {
      return ProbeResult(host: service, connected: false, detail: '端口有响应，服务连接超时');
    } catch (_) {
      return ProbeResult(host: service, connected: false, detail: '端口有响应，服务连接中断');
    } finally {
      tls?.close();
      if (tls == null && tunnel != null) {
        await tunnel.close();
      }
    }
  }

  Future<ProxyReport> _probeHostsIntoReport({
    required List<String> hosts,
    required String provider,
    required String mode,
    required String host,
    required int port,
    required String scheme,
    required bool? tun,
    ControllerEndpoint? endpoint,
    required bool allowUncontrolled,
    List<String> tunIncludePackages = const [],
    List<String> tunExcludePackages = const [],
  }) async {
    final traceHosts = <String>[];
    if (hosts.any((h) => h.startsWith('chatgpt.com'))) {
      traceHosts.add('chatgpt.com');
    }
    if (hosts.any((h) => h.startsWith('claude.ai'))) {
      traceHosts.add('claude.ai');
    }
    if (traceHosts.isEmpty) {
      traceHosts.add('www.cloudflare.com');
    }

    final useProxyHost = port > 0 ? host : null;
    final useProxyPort = port > 0 ? port : null;

    final traceFutures = {
      for (final th in traceHosts)
        th: probeExitRegion(
          traceHost: th,
          proxyHost: useProxyHost,
          proxyPort: useProxyPort,
          scheme: scheme,
        ),
    };

    final probeResults = await Future.wait(
      hosts.map(
        (h) => probeService(
          service: h,
          proxyHost: useProxyHost,
          proxyPort: useProxyPort,
          scheme: scheme,
          endpoint: endpoint,
          allowUncontrolled: allowUncontrolled,
        ),
      ),
    );

    final traces = <String, (String, int?)>{};
    for (final entry in traceFutures.entries) {
      traces[entry.key] = await entry.value;
    }

    final enriched = <String, ProbeResult>{};
    for (final pr in probeResults) {
      final dom = pr.host.split('/').first;
      final t = traces[dom];
      if (pr.region.isEmpty && t != null && t.$1.isNotEmpty) {
        enriched[pr.host] = pr.copyWith(region: t.$1);
      } else {
        enriched[pr.host] = pr;
      }
    }

    String region = '';
    int? latency;
    for (final th in traceHosts) {
      final t = traces[th];
      if (t != null) {
        if (region.isEmpty && t.$1.isNotEmpty) region = t.$1;
        latency ??= t.$2;
      }
    }
    if (region.isEmpty) {
      for (final pr in enriched.values) {
        if (pr.region.isNotEmpty) {
          region = pr.region;
          break;
        }
      }
    }
    if (latency == null) {
      final samples = enriched.values
          .where((p) => p.connected && p.latencyMs != null)
          .map((p) => p.latencyMs!)
          .toList();
      if (samples.isNotEmpty) {
        latency = (samples.reduce((a, b) => a + b) / samples.length).round();
      }
    }

    return ProxyReport(
      provider: provider,
      mode: mode,
      host: host,
      port: port,
      probes: enriched,
      tun: tun,
      endpoint: endpoint,
      region: region,
      latencyMs: latency,
      scheme: scheme,
      tunIncludePackages: tunIncludePackages,
      tunExcludePackages: tunExcludePackages,
    );
  }

  Future<DiscoveryResult> discoverAndCheck({
    required List<AiTarget> targets,
    required AppSettings settings,
    String controllerSecret = '',
    int? excludeRelayPort,
  }) async {
    final hosts = <String>{
      for (final t in targets) ...t.hosts,
    }.toList();
    final hints = <String>[];

    var netState = await bridge.getNetworkState();
    if (netState.platform == 'android') {
      final audited = await bridge.inspectPackages(geminiSuiteSpecs);
      if (audited.isNotEmpty) {
        netState = netState.copyWith(geminiPackages: audited);
      }
      if (netState.vpnBypassedForSelf) {
        hints.add(
          '检测到手机已开启 VPN 但本应用流量未进入 VPN，说明代理软件的「访问控制（白名单）」正在生效。'
          '若白名单仅勾选了 Google，Gemini 会因缺少 Google Play 服务 (gms/gsf) 及无网络权限的 bard 包而被拦截，请点击「Gemini 修复」查看解决方案。',
        );
      }
    }
    final installedRaw = await bridge.getInstalledApps(
      androidPackages: [
        for (final t in targets) ...[
          if (t.androidPackage.isNotEmpty) t.androidPackage,
          ...t.fallbackAndroidPackages,
        ],
        for (final p in knownMobileProxyApps)
          if (p.androidPackage.isNotEmpty) p.androidPackage,
      ],
      iosSchemes: [
        for (final t in targets)
          if (t.iosScheme.isNotEmpty) t.iosScheme,
        for (final p in knownMobileProxyApps)
          if (p.iosScheme.isNotEmpty) p.iosScheme,
      ],
    );

    final installedAiIds = <String>{
      for (final t in targets)
        if ((t.androidPackage.isNotEmpty &&
                installedRaw.contains(t.androidPackage)) ||
            t.fallbackAndroidPackages.any(installedRaw.contains) ||
            (t.iosScheme.isNotEmpty && installedRaw.contains(t.iosScheme)))
          t.id,
    };

    final detectedProxyApps = knownMobileProxyApps
        .where(
          (p) =>
              (p.androidPackage.isNotEmpty &&
                  installedRaw.contains(p.androidPackage)) ||
              (p.iosScheme.isNotEmpty && installedRaw.contains(p.iosScheme)),
        )
        .toList();

    if (settings.proxyMode == manualMode) {
      final manualPort = int.tryParse(settings.manualPort.trim()) ?? 0;
      final manualHost = settings.manualHost.trim().isEmpty
          ? '127.0.0.1'
          : settings.manualHost.trim();
      if (manualPort < 1 || manualPort > 65535) {
        return DiscoveryResult(
          reports: const [
            ProxyReport(
              provider: manualMode,
              mode: '',
              host: '127.0.0.1',
              port: 0,
              probes: {},
              issue: '请填写有效的代理端口（如 7890、10808 或 1080）。',
            ),
          ],
          detectedProxyApps: detectedProxyApps,
          installedAiIds: installedAiIds,
          networkState: netState,
          hints: hints,
        );
      }
      final kind = await classifyLocalPort(manualHost, manualPort);
      final scheme = kind == PortKind.socks5 ? 'socks5' : 'http';
      final rep = await _probeHostsIntoReport(
        hosts: hosts,
        provider: '手动代理 $manualHost:$manualPort',
        mode: scheme,
        host: manualHost,
        port: manualPort,
        scheme: scheme,
        tun: netState.vpnActive,
        allowUncontrolled: true,
      );
      return DiscoveryResult(
        reports: [rep],
        detectedProxyApps: detectedProxyApps,
        installedAiIds: installedAiIds,
        networkState: netState,
        hints: hints,
      );
    }

    final reports = <ProxyReport>[];
    final coveredPorts = <int>{if (excludeRelayPort != null) excludeRelayPort};

    final ctrlTargets = <(String, int, String, String)>[];
    final customCtrlPort = int.tryParse(settings.controllerPort.trim());
    final customCtrlHost = settings.controllerHost.trim().isEmpty
        ? '127.0.0.1'
        : settings.controllerHost.trim();
    if (customCtrlPort != null && customCtrlPort >= 1 && customCtrlPort <= 65535) {
      ctrlTargets.add((customCtrlHost, customCtrlPort, controllerSecret, '自定义控制接口'));
    }
    for (final port in _controllerPorts) {
      if (port == excludeRelayPort) continue;
      if (customCtrlHost == '127.0.0.1' && customCtrlPort == port) continue;
      final matched = detectedProxyApps
          .where((a) => a.defaultControllerPorts.contains(port))
          .map((a) => a.name)
          .toList();
      final label = matched.isNotEmpty ? matched.first : 'Clash 控制接口';
      ctrlTargets.add(('127.0.0.1', port, '', label));
    }

    final ctrlProbes = await Future.wait(
      ctrlTargets.map(
        (t) async => (
          t,
          await clashApi.probeController(t.$1, t.$2, secret: t.$3, name: t.$4),
        ),
      ),
    );

    final liveControllers = <ControllerEndpoint>[];
    for (final (target, res) in ctrlProbes) {
      if (res.endpoint != null) {
        liveControllers.add(res.endpoint!);
        coveredPorts.add(res.endpoint!.port);
      } else if (res.needsSecret) {
        hints.add(
          '检测到 ${target.$1}:${target.$2} 开放了 Clash 控制接口但需要密钥，可在「控制接口」中填写密钥以开启切节点与分流核验。',
        );
        coveredPorts.add(target.$2);
      }
    }

    for (final ep in liveControllers) {
      final cfg = await clashApi.getConfigs(ep);
      if (cfg == null) continue;
      final mixedPort = cfg['mixed-port'] is int ? cfg['mixed-port'] as int : 0;
      final httpPort = cfg['port'] is int ? cfg['port'] as int : 0;
      final socksPort = cfg['socks-port'] is int ? cfg['socks-port'] as int : 0;
      final tunMap = cfg['tun'];
      final tunEnabled =
          (tunMap is Map && tunMap['enable'] == true) || netState.vpnActive;
      final tunInclude =
          ClashApiClient.extractTunStringList(cfg, 'include-package');
      final tunExclude =
          ClashApiClient.extractTunStringList(cfg, 'exclude-package');

      int proxyPort = 0;
      String scheme = 'http';
      if (mixedPort > 0 && mixedPort <= 65535) {
        proxyPort = mixedPort;
      } else if (httpPort > 0 && httpPort <= 65535) {
        proxyPort = httpPort;
      } else if (socksPort > 0 && socksPort <= 65535) {
        proxyPort = socksPort;
        scheme = 'socks5';
      }

      if (proxyPort > 0) coveredPorts.add(proxyPort);
      final rep = await _probeHostsIntoReport(
        hosts: hosts,
        provider: ep.name,
        mode: (cfg['mode'] ?? 'rule').toString(),
        host: ep.host,
        port: proxyPort,
        scheme: proxyPort > 0 ? scheme : 'tun',
        tun: tunEnabled,
        endpoint: ep,
        allowUncontrolled: true,
        tunIncludePackages: tunInclude,
        tunExcludePackages: tunExclude,
      );
      reports.add(rep);
    }

    final portCandidates = <(String, int, String)>[];
    if (netState.httpProxyHost != null &&
        netState.httpProxyPort != null &&
        netState.httpProxyPort! > 0 &&
        !coveredPorts.contains(netState.httpProxyPort)) {
      portCandidates.add((netState.httpProxyHost!, netState.httpProxyPort!, '系统代理'));
    }
    if (netState.socksProxyHost != null &&
        netState.socksProxyPort != null &&
        netState.socksProxyPort! > 0 &&
        !coveredPorts.contains(netState.socksProxyPort)) {
      portCandidates
          .add((netState.socksProxyHost!, netState.socksProxyPort!, '系统 SOCKS5'));
    }
    for (final p in _proxyPorts) {
      if (coveredPorts.contains(p)) continue;
      if (portCandidates.any((c) => c.$1 == '127.0.0.1' && c.$2 == p)) continue;
      final matched = detectedProxyApps
          .where((a) => a.defaultProxyPorts.contains(p))
          .map((a) => a.name)
          .toList();
      final name = matched.isNotEmpty ? matched.first : '本机代理';
      portCandidates.add(('127.0.0.1', p, name));
    }

    if (reports.isEmpty && portCandidates.isNotEmpty) {
      final classified = await Future.wait(
        portCandidates.map((c) async => (c, await classifyLocalPort(c.$1, c.$2))),
      );
      for (final (cand, kind) in classified) {
        if (kind == PortKind.httpProxy || kind == PortKind.socks5) {
          final scheme = kind == PortKind.socks5 ? 'socks5' : 'http';
          final rep = await _probeHostsIntoReport(
            hosts: hosts,
            provider: cand.$3,
            mode: scheme,
            host: cand.$1,
            port: cand.$2,
            scheme: scheme,
            tun: netState.vpnActive,
            allowUncontrolled: true,
          );
          if (rep.hasHealthyProbe) {
            reports.add(rep);
            if (scheme == 'socks5') {
              hints.add(
                '检测到 ${cand.$3}（${cand.$1}:${cand.$2}）仅开放 SOCKS5 端口，已自动通过内置中继桥接为本地 HTTP 代理。',
              );
            }
            break;
          }
        }
      }
    }

    final anyHealthy = reports.any((r) => r.hasHealthyProbe);
    if (!anyHealthy) {
      if (netState.vpnActive) {
        final vpnLabel = detectedProxyApps.isNotEmpty
            ? '${detectedProxyApps.first.name} VPN'
            : (netState.platform == 'ios' ? '系统 VPN (utun)' : '系统 VPN (TUN)');
        final vpnRep = await _probeHostsIntoReport(
          hosts: hosts,
          provider: vpnLabel,
          mode: 'tun',
          host: '',
          port: 0,
          scheme: 'tun',
          tun: true,
          allowUncontrolled: true,
        );
        if (vpnRep.hasHealthyProbe && vpnRep.region.toUpperCase() != 'CN') {
          reports.add(vpnRep);
        } else {
          hints.add(
            '检测到手机已开启 VPN 网卡，但未能通过非中国大陆节点连通目标 AI 服务；请检查代理软件节点流量或是否处于直连模式。',
          );
        }
      } else {
        final (directReg, _) = await probeExitRegion(
          traceHost: 'chatgpt.com',
          timeout: const Duration(seconds: 2),
        );
        if (directReg.isNotEmpty && directReg.toUpperCase() != 'CN') {
          final routerRep = await _probeHostsIntoReport(
            hosts: hosts,
            provider: '透明代理 / 旁路由',
            mode: 'tun',
            host: '',
            port: 0,
            scheme: 'tun',
            tun: true,
            allowUncontrolled: true,
          );
          if (routerRep.hasHealthyProbe &&
              routerRep.region.isNotEmpty &&
              routerRep.region.toUpperCase() != 'CN') {
            reports.add(routerRep);
          }
        }
      }
    }

    return DiscoveryResult(
      reports: reports,
      detectedProxyApps: detectedProxyApps,
      installedAiIds: installedAiIds,
      networkState: netState,
      hints: hints,
    );
  }

  static Future<Uint8List> _readChunk(
    Socket socket, {
    required Duration timeout,
  }) async {
    final completer = Completer<Uint8List>();
    late StreamSubscription<Uint8List> sub;
    sub = socket.listen(
      (data) {
        if (!completer.isCompleted) {
          completer.complete(data);
          sub.cancel();
        }
      },
      onError: (_) {
        if (!completer.isCompleted) completer.complete(Uint8List(0));
      },
      onDone: () {
        if (!completer.isCompleted) completer.complete(Uint8List(0));
      },
      cancelOnError: true,
    );
    try {
      return await completer.future.timeout(timeout, onTimeout: () {
        sub.cancel();
        return Uint8List(0);
      });
    } catch (_) {
      await sub.cancel();
      return Uint8List(0);
    }
  }

  static bool _containsHeaderEnd(Uint8List bytes) {
    for (int i = 0; i + 3 < bytes.length; i++) {
      if (bytes[i] == 13 &&
          bytes[i + 1] == 10 &&
          bytes[i + 2] == 13 &&
          bytes[i + 3] == 10) {
        return true;
      }
    }
    return false;
  }

  static int? _parseHttpStatus(Uint8List bytes) {
    if (bytes.length < 12) return null;
    final lineEnd = bytes.indexOf(13);
    final firstLine = latin1.decode(
      lineEnd > 0
          ? bytes.sublist(0, lineEnd)
          : bytes.sublist(0, min(64, bytes.length)),
      allowInvalid: true,
    );
    final parts = firstLine.trim().split(RegExp(r'\s+'));
    if (parts.length < 2 || !parts[0].toUpperCase().startsWith('HTTP/')) {
      return null;
    }
    return int.tryParse(parts[1]);
  }
}
