class AiTarget {
  const AiTarget({
    required this.id,
    required this.name,
    required this.category,
    required this.hosts,
    required this.description,
    required this.webUrl,
    this.androidPackage = '',
    this.fallbackAndroidPackages = const [],
    this.iosScheme = '',
    this.iconAsset = '',
    this.custom = false,
  });

  final String id;
  final String name;
  final String category; // "app", "web", "api"
  final List<String> hosts;
  final String description;
  final String webUrl;
  final String androidPackage;
  final List<String> fallbackAndroidPackages;
  final String iosScheme;
  final String iconAsset;
  final bool custom;

  Map<String, dynamic> toJson() => {
        'id': id,
        'name': name,
        'category': category,
        'hosts': hosts,
        'description': description,
        'webUrl': webUrl,
        'androidPackage': androidPackage,
        'fallbackAndroidPackages': fallbackAndroidPackages,
        'iosScheme': iosScheme,
        'iconAsset': iconAsset,
        'custom': custom,
      };

  factory AiTarget.fromJson(Map<String, dynamic> json) {
    return AiTarget(
      id: (json['id'] ?? '').toString(),
      name: (json['name'] ?? '').toString(),
      category: (json['category'] ?? 'web').toString(),
      hosts: (json['hosts'] as List<dynamic>? ?? const [])
          .map((e) => e.toString())
          .where((e) => e.isNotEmpty)
          .toList(),
      description: (json['description'] ?? '自定义 AI 服务').toString(),
      webUrl: (json['webUrl'] ?? '').toString(),
      androidPackage: (json['androidPackage'] ?? '').toString(),
      fallbackAndroidPackages:
          (json['fallbackAndroidPackages'] as List<dynamic>? ?? const [])
              .map((e) => e.toString())
              .where((e) => e.isNotEmpty)
              .toList(),
      iosScheme: (json['iosScheme'] ?? '').toString(),
      iconAsset: (json['iconAsset'] ?? '').toString(),
      custom: json['custom'] == true,
    );
  }
}

class MobileProxyApp {
  const MobileProxyApp({
    required this.name,
    required this.family,
    this.androidPackage = '',
    this.iosScheme = '',
    this.defaultControllerPorts = const [],
    this.defaultProxyPorts = const [],
  });

  final String name;
  final String family; // "clash", "singbox", "v2ray", "http", "vpn"
  final String androidPackage;
  final String iosScheme;
  final List<int> defaultControllerPorts;
  final List<int> defaultProxyPorts;
}

class ControllerEndpoint {
  const ControllerEndpoint({
    required this.host,
    required this.port,
    this.secret = '',
    this.name = 'Clash API',
  });

  final String host;
  final int port;
  final String secret;
  final String name;

  String get address => '$host:$port';

  @override
  bool operator ==(Object other) =>
      other is ControllerEndpoint &&
      other.host.toLowerCase() == host.toLowerCase() &&
      other.port == port;

  @override
  int get hashCode => Object.hash(host.toLowerCase(), port);

  @override
  String toString() => 'ControllerEndpoint($host:$port, name=$name)';
}

class ProbeResult {
  const ProbeResult({
    required this.host,
    required this.connected,
    required this.detail,
    this.route = 'unknown',
    this.latencyMs,
    this.node = '',
    this.region = '',
  });

  final String host;
  final bool connected;
  final String detail;
  final String route; // "proxy", "http_proxy", "socks5_proxy", "tun_route", "direct", "unknown"
  final int? latencyMs;
  final String node;
  final String region;

  bool get passing => connected && passingRoutes.contains(route);

  ProbeResult copyWith({
    String? route,
    int? latencyMs,
    String? node,
    String? region,
  }) {
    return ProbeResult(
      host: host,
      connected: connected,
      detail: detail,
      route: route ?? this.route,
      latencyMs: latencyMs ?? this.latencyMs,
      node: node ?? this.node,
      region: region ?? this.region,
    );
  }
}

class ProxyReport {
  const ProxyReport({
    required this.provider,
    required this.mode,
    required this.host,
    required this.port,
    required this.probes,
    this.tun,
    this.endpoint,
    this.region = '',
    this.latencyMs,
    this.scheme = 'http', // "http", "socks5", "tun"
    this.issue = '',
    this.tunIncludePackages = const [],
    this.tunExcludePackages = const [],
  });

  final String provider;
  final String mode;
  final String host;
  final int port;
  final Map<String, ProbeResult> probes;
  final bool? tun;
  final ControllerEndpoint? endpoint;
  final String region;
  final int? latencyMs;
  final String scheme;
  final String issue;
  final List<String> tunIncludePackages;
  final List<String> tunExcludePackages;

  int get passingCount => probes.values.where((p) => p.passing).length;
  bool get hasHealthyProbe => passingCount > 0;
}

class SelectorGroup {
  const SelectorGroup({
    required this.name,
    required this.current,
    required this.nodes,
    required this.delays,
  });

  final String name;
  final String current;
  final List<String> nodes;
  final Map<String, int?> delays;
}

class HealthFinding {
  const HealthFinding({
    required this.category,
    required this.status, // "ok", "warn", "error"
    required this.title,
    required this.detail,
  });

  final String category;
  final String status;
  final String title;
  final String detail;
}

class AndroidPackageAudit {
  const AndroidPackageAudit({
    required this.packageName,
    required this.displayName,
    required this.role,
    required this.installed,
    this.isSystemApp = false,
    this.hasInternetPermission = true,
    this.hasLauncherActivity = false,
    this.essential = false,
  });

  final String packageName;
  final String displayName;
  final String role;
  final bool installed;
  final bool isSystemApp;
  final bool hasInternetPermission;
  final bool hasLauncherActivity;
  final bool essential;

  /// Clash/FlClash Access Control often hides apps without INTERNET permission (e.g. Gemini bard stub).
  bool get hiddenByNoInternetFilter => installed && !hasInternetPermission;

  /// Clash/FlClash Access Control hides system apps by default (e.g. Google Play Services / GSF).
  bool get hiddenBySystemFilter => installed && isSystemApp;
}

class GeminiPackageSpec {
  const GeminiPackageSpec({
    required this.packageName,
    required this.fallbackName,
    required this.role,
    this.essential = false,
  });

  final String packageName;
  final String fallbackName;
  final String role;
  final bool essential;
}

const List<GeminiPackageSpec> geminiSuiteSpecs = [
  GeminiPackageSpec(
    packageName: 'com.google.android.googlequicksearchbox',
    fallbackName: 'Google',
    role: 'Gemini 核心运行宿主与主要联网引擎',
    essential: true,
  ),
  GeminiPackageSpec(
    packageName: 'com.google.android.apps.bard',
    fallbackName: 'Gemini (bard)',
    role: 'Gemini 快捷入口壳应用（包名为 bard，常不含网络权限而被访问控制隐藏）',
    essential: true,
  ),
  GeminiPackageSpec(
    packageName: 'com.google.android.gms',
    fallbackName: 'Google Play 服务',
    role: 'OAuth 账号登录、令牌刷新与 Play Integrity 校验（系统应用）',
    essential: true,
  ),
  GeminiPackageSpec(
    packageName: 'com.google.android.gsf',
    fallbackName: 'Google 服务框架',
    role: 'Google 账号与设备注册基础服务（系统应用）',
    essential: true,
  ),
  GeminiPackageSpec(
    packageName: 'com.android.vending',
    fallbackName: 'Google Play 商店',
    role: '设备完整性与 Gemini Advanced 订阅校验',
    essential: false,
  ),
  GeminiPackageSpec(
    packageName: 'com.android.chrome',
    fallbackName: 'Chrome 浏览器',
    role: '账号登录自定义标签页与网页版 Gemini 回退通道',
    essential: false,
  ),
  GeminiPackageSpec(
    packageName: 'com.google.android.webview',
    fallbackName: 'Android System WebView',
    role: '内置登录验证与网页视图组件（系统应用）',
    essential: false,
  ),
];

class NativeNetworkState {
  const NativeNetworkState({
    required this.platform,
    required this.osVersion,
    required this.vpnActive,
    this.activeNetworkIsVpn = false,
    this.vpnBypassedForSelf = false,
    this.selfPackageName = 'com.aiproxylauncher.ai_proxy_launcher',
    this.geminiPackages = const [],
    this.vpnInterfaces = const [],
    this.httpProxyHost,
    this.httpProxyPort,
    this.socksProxyHost,
    this.socksProxyPort,
  });

  final String platform; // "android", "ios", "unknown"
  final String osVersion;
  final bool vpnActive;
  final bool activeNetworkIsVpn;
  final bool vpnBypassedForSelf;
  final String selfPackageName;
  final List<AndroidPackageAudit> geminiPackages;
  final List<String> vpnInterfaces;
  final String? httpProxyHost;
  final int? httpProxyPort;
  final String? socksProxyHost;
  final int? socksProxyPort;

  NativeNetworkState copyWith({
    List<AndroidPackageAudit>? geminiPackages,
  }) {
    return NativeNetworkState(
      platform: platform,
      osVersion: osVersion,
      vpnActive: vpnActive,
      activeNetworkIsVpn: activeNetworkIsVpn,
      vpnBypassedForSelf: vpnBypassedForSelf,
      selfPackageName: selfPackageName,
      geminiPackages: geminiPackages ?? this.geminiPackages,
      vpnInterfaces: vpnInterfaces,
      httpProxyHost: httpProxyHost,
      httpProxyPort: httpProxyPort,
      socksProxyHost: socksProxyHost,
      socksProxyPort: socksProxyPort,
    );
  }

  static const empty = NativeNetworkState(
    platform: 'unknown',
    osVersion: 'Unknown',
    vpnActive: false,
  );
}

class DiscoveryResult {
  const DiscoveryResult({
    required this.reports,
    required this.detectedProxyApps,
    required this.installedAiIds,
    required this.networkState,
    required this.hints,
  });

  final List<ProxyReport> reports;
  final List<MobileProxyApp> detectedProxyApps;
  final Set<String> installedAiIds;
  final NativeNetworkState networkState;
  final List<String> hints;
}

class AppSettings {
  const AppSettings({
    this.darkMode = true,
    this.guideDismissed = false,
    this.relayEnabled = false,
    this.proxyMode = autoMode,
    this.manualHost = '127.0.0.1',
    this.manualPort = '',
    this.controllerHost = '127.0.0.1',
    this.controllerPort = '',
    this.enabledPresets = const [],
    this.customTargets = const [],
  });

  final bool darkMode;
  final bool guideDismissed;
  final bool relayEnabled;
  final String proxyMode;
  final String manualHost;
  final String manualPort;
  final String controllerHost;
  final String controllerPort;
  final List<String> enabledPresets;
  final List<AiTarget> customTargets;

  AppSettings copyWith({
    bool? darkMode,
    bool? guideDismissed,
    bool? relayEnabled,
    String? proxyMode,
    String? manualHost,
    String? manualPort,
    String? controllerHost,
    String? controllerPort,
    List<String>? enabledPresets,
    List<AiTarget>? customTargets,
  }) {
    return AppSettings(
      darkMode: darkMode ?? this.darkMode,
      guideDismissed: guideDismissed ?? this.guideDismissed,
      relayEnabled: relayEnabled ?? this.relayEnabled,
      proxyMode: proxyMode ?? this.proxyMode,
      manualHost: manualHost ?? this.manualHost,
      manualPort: manualPort ?? this.manualPort,
      controllerHost: controllerHost ?? this.controllerHost,
      controllerPort: controllerPort ?? this.controllerPort,
      enabledPresets: enabledPresets ?? this.enabledPresets,
      customTargets: customTargets ?? this.customTargets,
    );
  }

  Map<String, dynamic> toJson() => {
        'darkMode': darkMode,
        'guideDismissed': guideDismissed,
        'relayEnabled': relayEnabled,
        'proxyMode': proxyMode,
        'manualHost': manualHost,
        'manualPort': manualPort,
        'controllerHost': controllerHost,
        'controllerPort': controllerPort,
        'enabledPresets': enabledPresets,
        'customTargets': customTargets.map((e) => e.toJson()).toList(),
      };

  factory AppSettings.fromJson(Map<String, dynamic> json) {
    return AppSettings(
      darkMode: json['darkMode'] != false,
      guideDismissed: json['guideDismissed'] == true,
      relayEnabled: json['relayEnabled'] == true,
      proxyMode: (json['proxyMode'] ?? autoMode).toString(),
      manualHost: (json['manualHost'] ?? '127.0.0.1').toString(),
      manualPort: (json['manualPort'] ?? '').toString(),
      controllerHost: (json['controllerHost'] ?? '127.0.0.1').toString(),
      controllerPort: (json['controllerPort'] ?? '').toString(),
      enabledPresets: (json['enabledPresets'] as List<dynamic>? ?? const [])
          .map((e) => e.toString())
          .toList(),
      customTargets: (json['customTargets'] as List<dynamic>? ?? const [])
          .whereType<Map<String, dynamic>>()
          .map(AiTarget.fromJson)
          .toList(),
    );
  }
}

const String autoMode = '自动检测';
const String manualMode = '手动指定端口';

const String oauthTokenEndpoint = 'oauth2.googleapis.com/token';
const String dailyCodeAssistEndpoint =
    'daily-cloudcode-pa.googleapis.com/v1internal:loadCodeAssist';
const String codexResponsesEndpoint = 'chatgpt.com/backend-api/codex/responses';

const Set<String> passingRoutes = {
  'proxy',
  'http_proxy',
  'socks5_proxy',
  'tun_route',
};

const Map<String, String> routeLabels = {
  'proxy': '节点代理',
  'http_proxy': 'HTTP 代理',
  'socks5_proxy': 'SOCKS5 代理',
  'tun_route': 'VPN / 透明路由',
  'direct': '直连',
  'unknown': '未确认出口',
};

const Map<String, String> probeShortLabels = {
  'accounts.google.com': 'Google 账号',
  oauthTokenEndpoint: 'Google OAuth',
  dailyCodeAssistEndpoint: 'Code Assist',
  'cloudcode-pa.googleapis.com': 'CloudCode',
  'businessaicode.googleapis.com': 'AI Code',
  'antigravity.google.com': 'Antigravity',
  'gemini.google.com': 'Gemini',
  'claude.ai': 'Claude',
  'chatgpt.com': 'ChatGPT',
  'ws.chatgpt.com': 'ChatGPT WS',
  'api.anthropic.com': 'Anthropic API',
  codexResponsesEndpoint: 'Codex WS',
  'api2.cursor.sh': 'Cursor API',
  'grok.com': 'Grok',
  'www.perplexity.ai': 'Perplexity',
  'api.openai.com': 'OpenAI API',
  'generativelanguage.googleapis.com': 'Gemini API',
};

const List<AiTarget> defaultMobileTargets = [
  AiTarget(
    id: 'chatgpt_app',
    name: 'ChatGPT App',
    category: 'app',
    hosts: ['chatgpt.com', 'ws.chatgpt.com'],
    description: '一键唤起手机 ChatGPT 客户端（未安装则开网页版）',
    webUrl: 'https://chatgpt.com/',
    androidPackage: 'com.openai.chatgpt',
    iosScheme: 'chatgpt',
    iconAsset: 'assets/icons/chatgpt.png',
  ),
  AiTarget(
    id: 'claude_app',
    name: 'Claude App',
    category: 'app',
    hosts: ['claude.ai', 'api.anthropic.com'],
    description: '一键唤起手机 Claude 客户端（未安装则开网页版）',
    webUrl: 'https://claude.ai/',
    androidPackage: 'com.anthropic.claude',
    iosScheme: 'claude',
    iconAsset: 'assets/icons/claude.png',
  ),
  AiTarget(
    id: 'gemini_app',
    name: 'Gemini App',
    category: 'app',
    hosts: ['gemini.google.com', 'accounts.google.com'],
    description: '一键唤起 Gemini / Google 客户端（含访问控制修复）',
    webUrl: 'https://gemini.google.com/app',
    androidPackage: 'com.google.android.apps.bard',
    fallbackAndroidPackages: ['com.google.android.googlequicksearchbox'],
    iosScheme: 'googlegemini',
    iconAsset: 'assets/icons/gemini.png',
  ),
  AiTarget(
    id: 'antigravity_web',
    name: 'Antigravity 网页',
    category: 'web',
    hosts: [
      'antigravity.google.com',
      'accounts.google.com',
      oauthTokenEndpoint,
      dailyCodeAssistEndpoint,
    ],
    description: '校验 Google 登录与 Antigravity 全链路接口',
    webUrl: 'https://antigravity.google.com/',
    iconAsset: 'assets/icons/antigravity_web.png',
  ),
  AiTarget(
    id: 'chatgpt_web',
    name: 'ChatGPT / Codex',
    category: 'web',
    hosts: ['chatgpt.com', 'ws.chatgpt.com', codexResponsesEndpoint],
    description: '网页版 + Codex WebSocket 实时通道核验',
    webUrl: 'https://chatgpt.com/',
    iconAsset: 'assets/icons/chatgpt_desktop.png',
  ),
  AiTarget(
    id: 'claude_web',
    name: 'Claude 网页 / API',
    category: 'web',
    hosts: ['claude.ai', 'api.anthropic.com'],
    description: '网页对话与 Anthropic API 双重连通性核验',
    webUrl: 'https://claude.ai/',
    iconAsset: 'assets/icons/claude_desktop.png',
  ),
  AiTarget(
    id: 'grok',
    name: 'Grok',
    category: 'app',
    hosts: ['grok.com'],
    description: '一键唤起 Grok App 或网页版',
    webUrl: 'https://grok.com/',
    androidPackage: 'ai.x.grok',
    iosScheme: 'grok',
    iconAsset: 'assets/icons/grok.png',
  ),
  AiTarget(
    id: 'perplexity',
    name: 'Perplexity',
    category: 'app',
    hosts: ['www.perplexity.ai'],
    description: '一键唤起 Perplexity App 或网页版',
    webUrl: 'https://www.perplexity.ai/',
    androidPackage: 'ai.perplexity.app.android',
    iosScheme: 'perplexity',
    iconAsset: 'assets/icons/perplexity.png',
  ),
];

const List<AiTarget> presetMobileTargets = [
  AiTarget(
    id: 'google_cloudcode',
    name: 'Google CloudCode API',
    category: 'api',
    hosts: [
      'accounts.google.com',
      oauthTokenEndpoint,
      dailyCodeAssistEndpoint,
      'cloudcode-pa.googleapis.com',
      'businessaicode.googleapis.com',
    ],
    description: '深度检测 Google AI 开发者与 OAuth 接口',
    webUrl: 'https://antigravity.google.com/',
    iconAsset: 'assets/icons/agent.png',
  ),
  AiTarget(
    id: 'cursor_api',
    name: 'Cursor API',
    category: 'api',
    hosts: ['api2.cursor.sh'],
    description: '检测 Cursor 后端出口与地区可用性',
    webUrl: 'https://cursor.com/',
    iconAsset: 'assets/icons/cursor.png',
  ),
  AiTarget(
    id: 'openai_api',
    name: 'OpenAI API',
    category: 'api',
    hosts: ['api.openai.com'],
    description: '检测 api.openai.com 连通性与延迟',
    webUrl: 'https://platform.openai.com/',
    iconAsset: 'assets/icons/codex_cli.png',
  ),
  AiTarget(
    id: 'gemini_api',
    name: 'Gemini API',
    category: 'api',
    hosts: ['generativelanguage.googleapis.com'],
    description: '检测 Google Generative Language API 连通性',
    webUrl: 'https://aistudio.google.com/',
    iconAsset: 'assets/icons/gemini_cli.png',
  ),
];

const List<MobileProxyApp> knownMobileProxyApps = [
  MobileProxyApp(
    name: 'FlClash',
    family: 'clash',
    androidPackage: 'com.follow.clash',
    iosScheme: 'flclash',
    defaultControllerPorts: [9090, 9097],
    defaultProxyPorts: [7890],
  ),
  MobileProxyApp(
    name: 'Clash Meta (CMFA)',
    family: 'clash',
    androidPackage: 'com.github.metacubex.clash.meta',
    iosScheme: 'clash',
    defaultControllerPorts: [9090],
    defaultProxyPorts: [7890],
  ),
  MobileProxyApp(
    name: 'Clash for Android',
    family: 'clash',
    androidPackage: 'com.github.kr328.clash',
    defaultControllerPorts: [9090],
    defaultProxyPorts: [7890],
  ),
  MobileProxyApp(
    name: 'Clash for Android (FOSS)',
    family: 'clash',
    androidPackage: 'com.github.kr328.clash.foss',
    defaultControllerPorts: [9090],
    defaultProxyPorts: [7890],
  ),
  MobileProxyApp(
    name: 'Stash',
    family: 'clash',
    iosScheme: 'stash',
    defaultControllerPorts: [9090],
    defaultProxyPorts: [7890],
  ),
  MobileProxyApp(
    name: 'Shadowrocket',
    family: 'http',
    iosScheme: 'shadowrocket',
    defaultProxyPorts: [1082, 7890],
  ),
  MobileProxyApp(
    name: 'Surge',
    family: 'http',
    iosScheme: 'surge',
    defaultControllerPorts: [6171],
    defaultProxyPorts: [6152],
  ),
  MobileProxyApp(
    name: 'Loon',
    family: 'http',
    iosScheme: 'loon',
    defaultProxyPorts: [7890],
  ),
  MobileProxyApp(
    name: 'Quantumult X',
    family: 'http',
    iosScheme: 'quantumult-x',
    defaultProxyPorts: [8888, 7890],
  ),
  MobileProxyApp(
    name: 'v2rayNG',
    family: 'v2ray',
    androidPackage: 'com.v2ray.ang',
    defaultProxyPorts: [10809, 10808],
  ),
  MobileProxyApp(
    name: 'Hiddify',
    family: 'singbox',
    androidPackage: 'app.hiddify.com',
    iosScheme: 'hiddify',
    defaultControllerPorts: [16756, 9090],
    defaultProxyPorts: [12334, 2334],
  ),
  MobileProxyApp(
    name: 'sing-box',
    family: 'singbox',
    androidPackage: 'io.nekohasekai.sfa',
    iosScheme: 'sing-box',
    defaultControllerPorts: [9090],
    defaultProxyPorts: [7890, 10808],
  ),
  MobileProxyApp(
    name: 'Karing',
    family: 'singbox',
    androidPackage: 'com.nebula.karing',
    iosScheme: 'karing',
    defaultControllerPorts: [9090],
    defaultProxyPorts: [3067, 7890],
  ),
  MobileProxyApp(
    name: 'NekoBox',
    family: 'singbox',
    androidPackage: 'moe.nb4a',
    defaultProxyPorts: [2080, 2081],
  ),
  MobileProxyApp(
    name: 'Clash Mi',
    family: 'clash',
    iosScheme: 'clash',
    defaultControllerPorts: [9090],
    defaultProxyPorts: [7890],
  ),
  MobileProxyApp(
    name: 'V2Box',
    family: 'v2ray',
    iosScheme: 'v2box',
    defaultProxyPorts: [10809, 7890],
  ),
  MobileProxyApp(
    name: 'Surfboard',
    family: 'http',
    androidPackage: 'com.getsurfboard',
    defaultProxyPorts: [7890],
  ),
  MobileProxyApp(
    name: 'Shadowsocks',
    family: 'http',
    androidPackage: 'com.github.shadowsocks',
    defaultProxyPorts: [1080],
  ),
  MobileProxyApp(
    name: 'WireGuard',
    family: 'vpn',
    androidPackage: 'com.wireguard.android',
  ),
];

