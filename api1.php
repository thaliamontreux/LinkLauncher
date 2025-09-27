<?php
/**
 * PentaStar Taskbar API (single file)
 * - PDO + prepared statements
 * - Rich errors with 'detail' when DEBUG=true
 * - Endpoints used by the Windows client:
 *   sanity, me, systems
 *   create_system, update_system, delete_system
 *   list_connections, create_connection, update_connection, delete_connection
 *   update_profile, change_password, rotate_token
 *   admin_list_users, admin_create_user, admin_update_user, admin_delete_user
 *   admin_list_groups, admin_create_group, admin_update_group, admin_delete_group
 *   admin_set_user_groups, admin_get_user_acl, admin_set_user_acl
 *   admin_disable_user, admin_set_role
 *
 * Tables expected (utf8mb4):
 *   users(id, username, password_hash, display_name, email, role, disabled, api_token,
 *         can_group, can_personal, created_at, updated_at)
 *   groups(id, name)
 *   user_groups(user_id, group_id)
 *   user_group_acl(user_id, group_id, perms)
 *   systems(id, name, admin_url, user_url, locked, archived, scope, owner_user_id,
 *           created_by, created_at, updated_at)
 *   system_groups(system_id, group_id)
 *   system_connections(id, system_id, type, host, port, username, path, params,
 *                      icon_id, order_index, created_by, created_at, updated_at)
 *   icons(...), icon_assets(...)  // optional for richer icons
 */

header('Content-Type: application/json; charset=utf-8');
header('X-Content-Type-Options: nosniff');

// ---------- CONFIG ----------
const DEBUG   = true;  // set to false in production
const DB_HOST = 'localhost';
const DB_NAME = 'taskapp1';
const DB_USER = 'taskapp1';
const DB_PASS = 'taskapp1';

// Permissions bit flags
const P_READ    = 1;
const P_WRITE   = 2;
const P_MODIFY  = 4;
const P_EDIT    = 8;
const P_DELETE  = 16;
const P_ARCHIVE = 32;

// ---------- UTIL ----------
function ok($data=[]) {
  echo json_encode(['ok'=>true] + $data, JSON_UNESCAPED_SLASHES); exit;
}
function bad($msg, $detail=null, $http=200) {
  http_response_code($http);
  $out = ['ok'=>false, 'error'=>$msg];
  if (DEBUG && $detail) $out['detail'] = $detail;
  echo json_encode($out, JSON_UNESCAPED_SLASHES); exit;
}
function req($k, $default=null) {
  return $_POST[$k] ?? $_GET[$k] ?? $default;
}
function now(): string { return gmdate('Y-m-d H:i:s'); }
function hash_pw($pw): string { return password_hash($pw, PASSWORD_DEFAULT); }
function rand_token(int $bytes=32): string { return bin2hex(random_bytes($bytes)); }

// PDO
function db(): PDO {
  static $pdo = null;
  if ($pdo) return $pdo;
  $dsn = 'mysql:host='.DB_HOST.';dbname='.DB_NAME.';charset=utf8mb4';
  $pdo = new PDO($dsn, DB_USER, DB_PASS, [
    PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
    PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
  ]);
  return $pdo;
}

// ---------- AUTH ----------
function get_user_by_token(?string $token) {
  if (!$token) return null;
  $st = db()->prepare("SELECT id, username, display_name, email, role, disabled,
                              can_group, can_personal, api_token
                       FROM users WHERE api_token = ?");
  $st->execute([$token]);
  $u = $st->fetch();
  if (!$u) return null;
  if ((int)$u['disabled'] === 1) return null;
  $u['group_ids'] = user_group_ids((int)$u['id']);
  return $u;
}
function user_group_ids(int $uid): array {
  $st = db()->prepare("SELECT group_id FROM user_groups WHERE user_id=?");
  $st->execute([$uid]);
  return array_map(fn($r)=>(int)$r['group_id'], $st->fetchAll());
}
function user_acl_map(int $uid): array {
  $st = db()->prepare("SELECT group_id, perms FROM user_group_acl WHERE user_id=?");
  $st->execute([$uid]);
  $out = [];
  foreach ($st as $r) $out[(int)$r['group_id']] = (int)$r['perms'];
  return $out;
}
function is_admin(array $u): bool { return ($u['role'] ?? 'user') === 'admin'; }

// ---------- PERMISSION HELPERS ----------
function system_visible_to_user(array $sys, array $u): bool {
  $scope = $sys['scope'];
  if ($scope === 'system') return true;
  if ($scope === 'user') return ((int)$sys['owner_user_id'] === (int)$u['id']) || is_admin($u);
  if ($scope === 'group') {
    if (is_admin($u)) return true;
    $gids = system_group_ids((int)$sys['id']);
    return count(array_intersect($gids, $u['group_ids'])) > 0;
  }
  return false;
}
function system_group_ids(int $system_id): array {
  $st = db()->prepare("SELECT group_id FROM system_groups WHERE system_id=?");
  $st->execute([$system_id]);
  return array_map(fn($r)=>(int)$r['group_id'], $st->fetchAll());
}
function user_can_edit_system(array $sys, array $u): bool {
  if (is_admin($u)) return true;
  if ((int)$sys['locked'] === 1) return false;
  $scope = $sys['scope'];
  if ($scope === 'user') return ((int)$sys['owner_user_id'] === (int)$u['id']);
  if ($scope === 'group') {
    // Need EDIT or MODIFY in ANY of the system's groups
    $gids = system_group_ids((int)$sys['id']);
    if (!$gids) return false;
    $acl = user_acl_map((int)$u['id']);
    foreach ($gids as $gid) {
      $p = $acl[$gid] ?? 0;
      if ($p & (P_EDIT|P_MODIFY)) return true;
    }
  }
  return false;
}
function user_can_create_system(string $scope, array $u, array $group_ids): bool {
  if ($scope === 'system') return is_admin($u);
  if ($scope === 'user')   return (int)($u['can_personal'] ?? 1) === 1;
  if ($scope === 'group') {
    if ((int)($u['can_group'] ?? 1) !== 1) return false;
    // must belong to all selected groups
    return empty(array_diff($group_ids, $u['group_ids']));
  }
  return false;
}

// ---------- SMALL QUERIES ----------
function load_system(int $id): ?array {
  $st = db()->prepare("SELECT * FROM systems WHERE id=?");
  $st->execute([$id]);
  $s = $st->fetch();
  return $s ?: null;
}
function load_connections_for_system(int $sid): array {
  $st = db()->prepare("SELECT id, system_id, type, host, port, username, path, params
                       FROM system_connections WHERE system_id=? ORDER BY order_index, id");
  $st->execute([$sid]);
  return $st->fetchAll();
}
function enrich_systems_with_connections(array $systems): array {
  foreach ($systems as &$s) {
    $s['connections'] = load_connections_for_system((int)$s['id']);
  }
  return $systems;
}

// ---------- ACTIONS ----------
try {
  $action = req('action', '');
  $token  = req('user_token', '');

  if ($action === 'sanity') {
    ok(['app'=>'pentastar-taskbar-api','time'=>now()]);
  }

  // Some actions require auth
  $user = null;
  $auth_required = !in_array($action, ['sanity'], true);
  if ($auth_required) {
    $user = get_user_by_token($token);
    if (!$user) bad("Unauthorized", "Invalid or disabled token", 401);
  }

  switch ($action) {

    // ------ Current user ------
    case 'me': {
      ok(['user' => [
        'id' => (int)$user['id'],
        'username' => $user['username'],
        'display_name' => $user['display_name'],
        'email' => $user['email'],
        'role' => $user['role'],
        'disabled' => (int)$user['disabled'],
        'can_group' => (int)$user['can_group'],
        'can_personal' => (int)$user['can_personal'],
        'group_ids' => $user['group_ids'],
      ]]);
    }

    // ------ Systems visible to user ------
    case 'systems': {
      // Pull all, then filter by visibility (keeps logic simple and clear)
      $st = db()->query("SELECT * FROM systems ORDER BY order_index, id");
      $all = $st->fetchAll();
      $vis = [];
      foreach ($all as $s) {
        if (system_visible_to_user($s, $user)) $vis[] = $s;
      }
      $vis = enrich_systems_with_connections($vis);
      ok(['systems'=>$vis]);
    }

    // ------ Connections ------
    case 'list_connections': {
      $sid = (int)req('system_id', 0);
      if (!$sid) bad("Missing system_id");
      $sys = load_system($sid);
      if (!$sys) bad("Not found", "system");
      if (!system_visible_to_user($sys, $user)) bad("Forbidden");
      ok(['connections'=>load_connections_for_system($sid)]);
    }

    case 'create_connection': {
      $sid = (int)req('system_id', 0);
      $type = req('type','');
      $host = req('host','');
      $port = (int)req('port', 0);
      $username = req('username', null);
      $path = req('path', null);
      $params = req('params', null);
      if (!$sid || !$type || !$host) bad("Missing required fields");
      $sys = load_system($sid);
      if (!$sys) bad("Not found","system");
      if (!user_can_edit_system($sys, $user)) bad("Forbidden");
      if ($port <= 0) $port = 0;
      $now = now();
      $st = db()->prepare("INSERT INTO system_connections
          (system_id,type,host,port,username,path,params,order_index,created_by,created_at,updated_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?)");
      $st->execute([$sid,$type,$host,$port,$username,$path,$params,0,(int)$user['id'],$now,$now]);
      ok(['id'=> (int)db()->lastInsertId()]);
    }

    case 'update_connection': {
      $id = (int)req('id', 0);
      if (!$id) bad("Missing id");
      $st = db()->prepare("SELECT * FROM system_connections WHERE id=?");
      $st->execute([$id]);
      $conn = $st->fetch();
      if (!$conn) bad("Not found", "connection");
      $sys = load_system((int)$conn['system_id']);
      if (!$sys) bad("Not found", "system");
      if (!user_can_edit_system($sys, $user)) bad("Forbidden");
      $type = req('type', $conn['type']);
      $host = req('host', $conn['host']);
      $port = (int)req('port', $conn['port']);
      $username = req('username', $conn['username']);
      $path = req('path', $conn['path']);
      $params = req('params', $conn['params']);
      $st2 = db()->prepare("UPDATE system_connections
                            SET type=?, host=?, port=?, username=?, path=?, params=?, updated_at=?
                            WHERE id=?");
      $st2->execute([$type,$host,$port,$username,$path,$params,now(),$id]);
      ok();
    }

    case 'delete_connection': {
      $id = (int)req('id', 0);
      if (!$id) bad("Missing id");
      $st = db()->prepare("SELECT * FROM system_connections WHERE id=?");
      $st->execute([$id]);
      $conn = $st->fetch();
      if (!$conn) bad("Not found", "connection");
      $sys = load_system((int)$conn['system_id']);
      if (!$sys) bad("Not found", "system");
      if (!user_can_edit_system($sys, $user)) bad("Forbidden");
      db()->prepare("DELETE FROM system_connections WHERE id=?")->execute([$id]);
      ok();
    }

    // ------ Systems CRUD ------
    case 'create_system': {
      $name = req('name','');
      $scope = req('scope','system');
      $locked = (int)req('locked', 0);
      $archived = (int)req('archived', 0);
      $admin_url = req('admin_url', null);
      $user_url = req('user_url', null);
      $group_ids = array_filter(array_map('intval', explode(',', req('group_ids',''))));
      if (!$name) bad("Name required");
      if (!in_array($scope, ['system','group','user'], true)) bad("Invalid scope");
      if (!user_can_create_system($scope, $user, $group_ids)) bad("Forbidden");

      // only admins may set locked on create for safety
      if (!is_admin($user)) $locked = 0;

      $now = now();
      $st = db()->prepare("INSERT INTO systems
        (name, admin_url, user_url, locked, archived, scope, owner_user_id, created_by, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)");
      $owner = ($scope==='user') ? (int)$user['id'] : 0;
      $st->execute([$name,$admin_url,$user_url,$locked,$archived,$scope,$owner,(int)$user['id'],$now,$now]);
      $sid = (int)db()->lastInsertId();

      if ($scope === 'group' && $group_ids) {
        $ins = db()->prepare("INSERT IGNORE INTO system_groups(system_id, group_id) VALUES(?,?)");
        foreach ($group_ids as $gid) $ins->execute([$sid, $gid]);
      }
      ok(['id'=>$sid]);
    }

    case 'update_system': {
      $id = (int)req('id', 0);
      if (!$id) bad("Missing id");
      $sys = load_system($id);
      if (!$sys) bad("Not found","system");
      if (!user_can_edit_system($sys, $user)) bad("Forbidden");

      $name = req('name', $sys['name']);
      $admin_url = req('admin_url', $sys['admin_url']);
      $user_url  = req('user_url',  $sys['user_url']);
      $archived  = (int)req('archived', $sys['archived']);
      $scope     = req('scope', $sys['scope']); // scope change allowed? keep simple: yes if creator/admin

      // locked may only be set by admin
      $locked = (int)$sys['locked'];
      if (is_admin($user)) $locked = (int)req('locked', $locked);

      if (!in_array($scope, ['system','group','user'], true)) bad("Invalid scope");
      $st = db()->prepare("UPDATE systems SET name=?, admin_url=?, user_url=?, locked=?, archived=?, scope=?, updated_at=? WHERE id=?");
      $st->execute([$name,$admin_url,$user_url,$locked,$archived,$scope,now(),$id]);

      if ($scope === 'group') {
        $group_ids = array_filter(array_map('intval', explode(',', req('group_ids',''))));
        $del = db()->prepare("DELETE FROM system_groups WHERE system_id=?");
        $del->execute([$id]);
        if ($group_ids) {
          $ins = db()->prepare("INSERT IGNORE INTO system_groups(system_id, group_id) VALUES(?,?)");
          foreach ($group_ids as $gid) $ins->execute([$id, $gid]);
        }
      } else {
        db()->prepare("DELETE FROM system_groups WHERE system_id=?")->execute([$id]);
      }
      ok();
    }

    case 'delete_system': {
      $id = (int)req('id', 0);
      if (!$id) bad("Missing id");
      $sys = load_system($id);
      if (!$sys) bad("Not found","system");
      if (!user_can_edit_system($sys, $user)) bad("Forbidden");
      db()->prepare("DELETE FROM systems WHERE id=?")->execute([$id]);
      ok();
    }

    // ------ Profile / password / token ------
    case 'update_profile': {
      $display_name = req('display_name', $user['display_name']);
      $email = req('email', $user['email']);
      $st = db()->prepare("UPDATE users SET display_name=?, email=?, updated_at=? WHERE id=?");
      $st->execute([$display_name, $email, now(), (int)$user['id']]);
      ok();
    }

    case 'change_password': {
      $old = req('old_password',''); $new = req('new_password','');
      if (!$old || !$new) bad("Missing password");
      $st = db()->prepare("SELECT password_hash FROM users WHERE id=?");
      $st->execute([(int)$user['id']]);
      $row = $st->fetch();
      if (!$row || !password_verify($old, $row['password_hash'])) bad("Invalid current password");
      $st2 = db()->prepare("UPDATE users SET password_hash=?, updated_at=? WHERE id=?");
      $st2->execute([hash_pw($new), now(), (int)$user['id']]);
      ok();
    }

    case 'rotate_token': {
      $target_id = (int)req('id', (int)$user['id']);
      if (!is_admin($user) && $target_id !== (int)$user['id']) bad("Forbidden");
      $st = db()->prepare("SELECT id, role FROM users WHERE id=?");
      $st->execute([$target_id]); $tu = $st->fetch();
      if (!$tu) bad("Not found","user");
      $new = rand_token(32);
      $st2 = db()->prepare("UPDATE users SET api_token=?, updated_at=? WHERE id=?");
      $st2->execute([$new, now(), $target_id]);
      // TODO: send email if address present (mail())
      ok(['new_token'=>$new]);
    }

    // ------ Admin: groups ------
    case 'admin_list_groups': {
      if (!is_admin($user)) bad("Forbidden");
      $gs = db()->query("SELECT id,name FROM groups ORDER BY name")->fetchAll();
      ok(['groups'=>$gs]);
    }

    case 'admin_create_group': {
      if (!is_admin($user)) bad("Forbidden");
      $name = trim((string)req('name',''));
      if ($name==='') bad("Name required");
      $st = db()->prepare("INSERT INTO groups(name) VALUES(?)");
      $st->execute([$name]);
      ok(['id'=>(int)db()->lastInsertId()]);
    }

    case 'admin_update_group': {
      if (!is_admin($user)) bad("Forbidden");
      $id = (int)req('id',0);
      $name = trim((string)req('name',''));
      if (!$id || $name==='') bad("Missing fields");
      $st = db()->prepare("UPDATE groups SET name=? WHERE id=?");
      $st->execute([$name,$id]);
      ok();
    }

    case 'admin_delete_group': {
      if (!is_admin($user)) bad("Forbidden");
      $id = (int)req('id',0);
      if (!$id) bad("Missing id");
      // remove memberships + acl first (FKs might cascade, but be explicit)
      db()->prepare("DELETE FROM user_groups WHERE group_id=?")->execute([$id]);
      db()->prepare("DELETE FROM user_group_acl WHERE group_id=?")->execute([$id]);
      db()->prepare("DELETE FROM system_groups WHERE group_id=?")->execute([$id]);
      db()->prepare("DELETE FROM groups WHERE id=?")->execute([$id]);
      ok();
    }

    // ------ Admin: users ------
    case 'admin_list_users': {
      if (!is_admin($user)) bad("Forbidden");
      $us = db()->query("SELECT id, username, display_name, email, role, disabled, can_group, can_personal
                         FROM users ORDER BY id")->fetchAll();
      // attach group_ids for convenience
      foreach ($us as &$u2) {
        $u2['group_ids'] = user_group_ids((int)$u2['id']);
      }
      ok(['users'=>$us]);
    }

    case 'admin_create_user': {
      if (!is_admin($user)) bad("Forbidden");
      $username = trim((string)req('username',''));
      $display  = trim((string)req('display_name',''));
      $email    = trim((string)req('email',''));
      $password = (string)req('password','');
      $can_group    = (int)req('can_group',1);
      $can_personal = (int)req('can_personal',1);
      if ($username==='' || $display==='' || $password==='') bad("Missing fields");
      $st = db()->prepare("INSERT INTO users(username, password_hash, display_name, email, role, disabled,
                                             api_token, can_group, can_personal, created_at, updated_at)
                           VALUES(?,?,?,?, 'user', 0, ?, ?, ?, ?, ?)");
      $tok = rand_token(32); $t = now();
      $st->execute([$username, hash_pw($password), $display, $email, $tok, $can_group, $can_personal, $t, $t]);
      // TODO: mail token to user if email present
      ok(['id'=>(int)db()->lastInsertId(), 'new_token'=>$tok]);
    }

    case 'admin_update_user': {
      if (!is_admin($user)) bad("Forbidden");
      $id = (int)req('id',0);
      if (!$id) bad("Missing id");
      $display = req('display_name', null);
      $email   = req('email', null);
      $can_group = req('can_group', null);
      $can_personal = req('can_personal', null);
      $parts = []; $vals=[];
      if ($display !== null) { $parts[]="display_name=?"; $vals[]=$display; }
      if ($email !== null)   { $parts[]="email=?";        $vals[]=$email; }
      if ($can_group !== null)    { $parts[]="can_group=?";    $vals[]=(int)$can_group; }
      if ($can_personal !== null) { $parts[]="can_personal=?"; $vals[]=(int)$can_personal; }
      if (!$parts) ok(); // nothing to do
      $parts[]="updated_at=?"; $vals[]=now(); $vals[]=$id;
      $sql = "UPDATE users SET ".implode(",", $parts)." WHERE id=?";
      db()->prepare($sql)->execute($vals);
      ok();
    }

    case 'admin_delete_user': {
      if (!is_admin($user)) bad("Forbidden");
      $id = (int)req('id',0);
      if (!$id) bad("Missing id");
      if ($id === (int)$user['id']) bad("Cannot delete your own account.");
      // avoid deleting other admins lightly? (allow, but check at least one admin remains)
      $admins = db()->query("SELECT COUNT(*) c FROM users WHERE role='admin'")->fetch()['c'] ?? 0;
      $stRole = db()->prepare("SELECT role FROM users WHERE id=?"); $stRole->execute([$id]);
      $role = $stRole->fetchColumn();
      if ($role==='admin' && (int)$admins <= 1) bad("Cannot delete the last admin.");
      db()->prepare("DELETE FROM users WHERE id=?")->execute([$id]);
      ok();
    }

    case 'admin_set_user_groups': {
      if (!is_admin($user)) bad("Forbidden");
      $id = (int)req('id',0);
      $group_ids = array_filter(array_map('intval', explode(',', req('group_ids',''))));
      if (!$id) bad("Missing id");
      $del = db()->prepare("DELETE FROM user_groups WHERE user_id=?");
      $del->execute([$id]);
      if ($group_ids) {
        $ins = db()->prepare("INSERT IGNORE INTO user_groups(user_id, group_id) VALUES(?,?)");
        foreach ($group_ids as $gid) $ins->execute([$id,$gid]);
      }
      ok();
    }

    case 'admin_get_user_acl': {
      if (!is_admin($user)) bad("Forbidden");
      $id = (int)req('id',0);
      if (!$id) bad("Missing id");
      // return ACL rows aligned to existing groups (include zero-perm rows)
      $gs = db()->query("SELECT id,name FROM groups ORDER BY name")->fetchAll();
      $acl = user_acl_map($id);
      $out = [];
      foreach ($gs as $g) {
        $gid = (int)$g['id'];
        $out[] = ['group_id'=>$gid, 'name'=>$g['name'], 'perms'=> (int)($acl[$gid] ?? 0)];
      }
      ok(['acl'=>$out]);
    }

    case 'admin_set_user_acl': {
      if (!is_admin($user)) bad("Forbidden");
      $id = (int)req('id',0);
      $acl_json = req('acl','[]');
      $items = json_decode($acl_json, true);
      if (!is_array($items)) bad("Invalid acl");
      $del = db()->prepare("DELETE FROM user_group_acl WHERE user_id=?");
      $del->execute([$id]);
      $ins = db()->prepare("INSERT INTO user_group_acl(user_id, group_id, perms) VALUES(?,?,?)");
      foreach ($items as $row) {
        $gid = (int)($row['group_id'] ?? 0);
        $perms = (int)($row['perms'] ?? 0);
        if ($gid>0) $ins->execute([$id,$gid,$perms]);
      }
      ok();
    }

    case 'admin_disable_user': {
      if (!is_admin($user)) bad("Forbidden");
      $id = (int)req('id',0);
      $disabled = (int)req('disabled',0);
      if (!$id) bad("Missing id");
      if ($id === (int)$user['id']) bad("You cannot disable your own account.");
      // prevent disabling the last admin
      $stRole = db()->prepare("SELECT role FROM users WHERE id=?"); $stRole->execute([$id]);
      $role = $stRole->fetchColumn();
      if ($role==='admin' && $disabled===1) {
        $admins = db()->query("SELECT COUNT(*) c FROM users WHERE role='admin' AND disabled=0")->fetch()['c'] ?? 0;
        if ((int)$admins <= 1) bad("Cannot disable the last active admin.");
      }
      db()->prepare("UPDATE users SET disabled=?, updated_at=? WHERE id=?")->execute([$disabled, now(), $id]);
      ok();
    }

    case 'admin_set_role': {
      if (!is_admin($user)) bad("Forbidden");
      $id = (int)req('id',0);
      $role = req('role','user');
      if (!in_array($role, ['user','admin'], true)) bad("Invalid role");
      if ($id === (int)$user['id'] && $role!=='admin') bad("You cannot remove your own admin role.");
      // prevent demoting the last admin
      $stRole = db()->prepare("SELECT role FROM users WHERE id=?"); $stRole->execute([$id]);
      $current = $stRole->fetchColumn();
      if ($current==='admin' && $role==='user') {
        $admins = db()->query("SELECT COUNT(*) c FROM users WHERE role='admin' AND disabled=0")->fetch()['c'] ?? 0;
        if ((int)$admins <= 1) bad("Cannot demote the last active admin.");
      }
      db()->prepare("UPDATE users SET role=?, updated_at=? WHERE id=?")->execute([$role, now(), $id]);
      ok();
    }

    default:
      bad("Unknown action", $action, 400);
  }

} catch (Throwable $e) {
  bad("Server error", $e->getMessage(), 500);
}
