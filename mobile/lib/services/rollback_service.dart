import '../models/models.dart';
import 'clash_api.dart';

class MobileSessionChange {
  const MobileSessionChange({
    required this.id,
    required this.kind, // "node" or "tun_include"
    required this.provider,
    required this.endpoint,
    required this.target,
    required this.summary,
    this.previousNode = '',
    this.currentNode = '',
    this.previousPackages = const [],
    this.currentPackages = const [],
  });

  final String id;
  final String kind;
  final String provider;
  final ControllerEndpoint endpoint;
  final String target;
  final String summary;
  final String previousNode;
  final String currentNode;
  final List<String> previousPackages;
  final List<String> currentPackages;
}

/// Tracks reversible session-level proxy API modifications made by the mobile app
/// (first-write-wins for original values) so users can roll back cleanly to isolate issues.
class MobileRollbackService {
  final Map<String, MobileSessionChange> _changes = {};

  List<MobileSessionChange> get changes => _changes.values.toList(growable: false);
  bool get hasChanges => _changes.isNotEmpty;
  int get count => _changes.length;

  void recordNodeSwitch({
    required ControllerEndpoint endpoint,
    required String groupName,
    required String previousNode,
    required String newNode,
  }) {
    if (groupName.isEmpty || previousNode.isEmpty || newNode.isEmpty || previousNode == newNode) {
      return;
    }
    final id = 'node:${endpoint.address}:$groupName';
    final existing = _changes[id];
    final origPrev = existing != null ? existing.previousNode : previousNode;
    if (origPrev == newNode) {
      _changes.remove(id);
      return;
    }
    _changes[id] = MobileSessionChange(
      id: id,
      kind: 'node',
      provider: endpoint.name,
      endpoint: endpoint,
      target: groupName,
      previousNode: origPrev,
      currentNode: newNode,
      summary: '将分流组「$groupName」从「$newNode」切回原节点「$origPrev」',
    );
  }

  void recordTunIncludePackageChange({
    required ControllerEndpoint endpoint,
    required List<String> previousPackages,
    required List<String> newPackages,
  }) {
    final prevList = List<String>.from(previousPackages);
    final newList = List<String>.from(newPackages);
    final id = 'tun_include:${endpoint.address}';
    final existing = _changes[id];
    final origPrev = existing != null ? existing.previousPackages : prevList;
    if (_sameStringList(origPrev, newList)) {
      _changes.remove(id);
      return;
    }
    _changes[id] = MobileSessionChange(
      id: id,
      kind: 'tun_include',
      provider: endpoint.name,
      endpoint: endpoint,
      target: 'TUN include-package',
      previousPackages: origPrev,
      currentPackages: newList,
      summary: '将 ${endpoint.name} 的 TUN 访问控制白名单恢复为修改前的 ${origPrev.length} 个应用包',
    );
  }

  Future<(bool, String)> undoChange(
    MobileSessionChange change,
    ClashApiClient clashApi,
  ) async {
    if (change.kind == 'node') {
      final ok = await clashApi.switchNode(
        change.endpoint,
        change.target,
        change.previousNode,
      );
      if (ok) {
        _changes.remove(change.id);
        return (true, '已将分流组「${change.target}」切回「${change.previousNode}」');
      }
      return (false, '无法通过控制接口将「${change.target}」切回「${change.previousNode}」');
    }
    if (change.kind == 'tun_include') {
      final ok = await clashApi.patchTunIncludePackages(
        change.endpoint,
        change.previousPackages,
      );
      if (ok) {
        _changes.remove(change.id);
        return (true, '已恢复 ${change.provider} 的原 TUN 访问控制白名单');
      }
      return (false, '无法通过控制接口恢复 ${change.provider} 的 TUN 访问控制白名单');
    }
    return (false, '暂不支持撤销该项修改');
  }

  Future<(List<String>, List<String>)> undoAll(ClashApiClient clashApi) async {
    final done = <String>[];
    final errors = <String>[];
    for (final change in changes) {
      final (ok, msg) = await undoChange(change, clashApi);
      if (ok) {
        done.add(msg);
      } else {
        errors.add(msg);
      }
    }
    return (done, errors);
  }

  void clear() => _changes.clear();

  static bool _sameStringList(List<String> a, List<String> b) {
    if (a.length != b.length) return false;
    for (int i = 0; i < a.length; i++) {
      if (a[i] != b[i]) return false;
    }
    return true;
  }
}
