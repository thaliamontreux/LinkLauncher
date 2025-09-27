<?php
// install.php — Browser-based installer for PentaStarLauncher API + DB
// Place this file in your web server's /api/ directory and open it in a browser.
// It will: create DB + user, create tables, seed data, write config.php and api1.php.

declare(strict_types=1);
ini_set('display_errors', '1');
error_reporting(E_ALL);

function h($s){ return htmlspecialchars((string)$s, ENT_QUOTES|ENT_SUBSTITUTE, 'UTF-8'); }
function rand_hex(int $bytes=32): string { return bin2hex(random_bytes($bytes)); }
function rand_password(int $len=20): string {
  $alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#$%*-_=+';
  $out=''; for($i=0;$i<$len;$i++){ $out.=$alphabet[random_int(0,strlen($alphabet)-1)]; } return $out;
}
function http_posted(): bool { return ($_SERVER['REQUEST_METHOD'] ?? 'GET') === 'POST'; }
function has_ext(string $ext): bool { return extension_loaded($ext); }

$errors = [];
$okmsg  = '';
$results = [
  'app_db_user' => '',
  'app_db_pass' => '',
  'admin_user'  => '',
  'admin_token' => '',
  'db_name'     => '',
];

if (http_posted()) {
  // Gather inputs
  $db_host = trim($_POST['db_host'] ?? 'localhost');
  $db_port = (int)($_POST['db_port'] ?? 3306);
  $root_user = trim($_POST['root_user'] ?? 'root');
  $root_pass = (string)($_POST['root_pass'] ?? '');
  $db_name = trim($_POST['db_name'] ?? 'taskapp1');
  $app_user = trim($_POST['app_user'] ?? 'taskapp1_app');
  $app_pass = trim($_POST['app_pass'] ?? '') ?: rand_password(24);

  $admin_username = trim($_POST['admin_username'] ?? 'admin');
  $admin_display  = trim($_POST['admin_display'] ?? 'Administrator');
  $admin_email    = trim($_POST['admin_email'] ?? '');
  $admin_password = trim($_POST['admin_password'] ?? '') ?: rand_password(16);
  $admin_token    = rand_hex(32);

  $mail_from  = trim($_POST['mail_from'] ?? ('no-reply@' . ($_SERVER['HTTP_HOST'] ?? 'localhost')));
  $mail_sender= trim($_POST['mail_sender'] ?? 'PentaStar Launcher');

  $base_dir = rtrim(str_replace('\\','/',__DIR__),'/'); // directory where install.php resides
  $config_path = $base_dir . '/config.php';
  $api_path    = $base_dir . '/api1.php';

  // Preflight checks
  if (!has_ext('pdo') || !has_ext('pdo_mysql')) $errors[] = "PDO + pdo_mysql extension required.";
  if (!function_exists('password_hash')) $errors[] = "password_hash() not available.";
  if (!function_exists('random_bytes')) $errors[] = "random_bytes() not available.";
  if (!is_writable($base_dir)) $errors[] = "Directory is not writable: " . h($base_dir);

  if (!$errors) {
    try {
      // Connect as root/admin
      $dsn_root = "mysql:host={$db_host};port={$db_port};charset=utf8mb4";
      $pdo_root = new PDO($dsn_root, $root_user, $root_pass, [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
      ]);

      // Create DB
      $pdo_root->exec("CREATE DATABASE IF NOT EXISTS `{$db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci");

      // Create app user (localhost and %)
      $qUser = $pdo_root->prepare("CREATE USER IF NOT EXISTS :user_local@'localhost' IDENTIFIED BY :pwd");
      $qUser->bindValue(':user_local', $app_user);
      $qUser->bindValue(':pwd', $app_pass);
      try { $qUser->execute(); } catch(Throwable $e) { /* MySQL 8 doesn't allow bind for user; fallback */ 
        $pdo_root->exec("CREATE USER IF NOT EXISTS `{$app_user}`@'localhost' IDENTIFIED BY " . $pdo_root->quote($app_pass));
      }
      try { $pdo_root->exec("CREATE USER IF NOT EXISTS `{$app_user}`@'%' IDENTIFIED BY " . $pdo_root->quote($app_pass)); } catch(Throwable $e){}

      // Grants
      $pdo_root->exec("GRANT SELECT,INSERT,UPDATE,DELETE,CREATE,ALTER,INDEX,REFERENCES ON `{$db_name}`.* TO `{$app_user}`@'localhost'");
      $pdo_root->exec("GRANT SELECT,INSERT,UPDATE,DELETE,CREATE,ALTER,INDEX,REFERENCES ON `{$db_name}`.* TO `{$app_user}`@'%'");
      $pdo_root->exec("FLUSH PRIVILEGES");

      // Connect as app user to create schema
      $dsn = "mysql:host={$db_host};port={$db_port};dbname={$db_name};charset=utf8mb4";
      $pdo = new PDO($dsn, $app_user, $app_pass, [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
      ]);

      // Create tables in dependency order
      $schema = [
"CREATE TABLE IF NOT EXISTS users (
  id INT AUTO_INCREMENT PRIMARY KEY,
  username VARCHAR(191) NOT NULL UNIQUE,
  password_hash VARCHAR(255) NOT NULL,
  display_name VARCHAR(255) NOT NULL,
  email VARCHAR(255) DEFAULT NULL,
  role ENUM('user','admin') NOT NULL DEFAULT 'user',
  token CHAR(64) NOT NULL UNIQUE,
  disabled TINYINT(1) NOT NULL DEFAULT 0,
  can_group TINYINT(1) NOT NULL DEFAULT 1,
  can_personal TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci",
"CREATE TABLE IF NOT EXISTS groups (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(191) NOT NULL UNIQUE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci",
"CREATE TABLE IF NOT EXISTS user_groups (
  user_id INT NOT NULL,
  group_id INT NOT NULL,
  PRIMARY KEY (user_id, group_id),
  CONSTRAINT fk_ug_user  FOREIGN KEY (user_id)  REFERENCES users(id)  ON DELETE CASCADE,
  CONSTRAINT fk_ug_group FOREIGN KEY (group_id) REFERENCES groups(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci",
"CREATE TABLE IF NOT EXISTS user_group_acl (
  user_id  INT NOT NULL,
  group_id INT NOT NULL,
  perms    INT UNSIGNED NOT NULL DEFAULT 0,
  PRIMARY KEY (user_id, group_id),
  CONSTRAINT fk_uga_user  FOREIGN KEY (user_id)  REFERENCES users(id)  ON DELETE CASCADE,
  CONSTRAINT fk_uga_group FOREIGN KEY (group_id) REFERENCES groups(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci",
"CREATE TABLE IF NOT EXISTS icons (
  id INT AUTO_INCREMENT PRIMARY KEY,
  icon_key VARCHAR(64) NOT NULL UNIQUE,
  label_text VARCHAR(16) NOT NULL,
  bg_color_hex VARCHAR(7) NOT NULL DEFAULT '#1976D2',
  fg_color_hex VARCHAR(7) NOT NULL DEFAULT '#FFFFFF',
  scope ENUM('default','custom') NOT NULL DEFAULT 'default',
  has_assets TINYINT(1) NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci",
"CREATE TABLE IF NOT EXISTS icon_assets (
  id INT AUTO_INCREMENT PRIMARY KEY,
  icon_id INT NOT NULL,
  width INT NOT NULL,
  height INT NOT NULL,
  format ENUM('png') NOT NULL DEFAULT 'png',
  mime_type VARCHAR(64) NOT NULL DEFAULT 'image/png',
  sha256 CHAR(64) NOT NULL,
  bytes LONGBLOB NOT NULL,
  UNIQUE KEY uniq_icon_size (icon_id, width, height, format),
  CONSTRAINT fk_iconasset_icon FOREIGN KEY (icon_id) REFERENCES icons(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci",
"CREATE TABLE IF NOT EXISTS systems (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  admin_url TEXT NULL,
  user_url  TEXT NULL,
  icon_id INT NULL,
  order_index INT NOT NULL DEFAULT 0,
  created_by INT NOT NULL,
  owner_user_id INT NOT NULL,
  scope ENUM('system','group','user') NOT NULL DEFAULT 'system',
  locked TINYINT(1) NOT NULL DEFAULT 0,
  archived TINYINT(1) NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_sys_owner FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci",
"CREATE TABLE IF NOT EXISTS system_groups (
  system_id INT NOT NULL,
  group_id INT NOT NULL,
  PRIMARY KEY (system_id, group_id),
  CONSTRAINT fk_sysgrp_sys FOREIGN KEY (system_id) REFERENCES systems(id) ON DELETE CASCADE,
  CONSTRAINT fk_sysgrp_grp FOREIGN KEY (group_id) REFERENCES groups(id)  ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci",
"CREATE TABLE IF NOT EXISTS system_connections (
  id INT AUTO_INCREMENT PRIMARY KEY,
  system_id INT NOT NULL,
  type ENUM('http','https','ssh','telnet','ftp','ftps','sftp','scp','rdp') NOT NULL,
  host VARCHAR(255) NOT NULL,
  port INT NOT NULL,
  username VARCHAR(255) NULL,
  path VARCHAR(1024) NULL,
  params VARCHAR(1024) NULL,
  icon_id INT NULL,
  order_index INT NOT NULL DEFAULT 0,
  created_by INT NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_conn_sys FOREIGN KEY (system_id) REFERENCES systems(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci",
      ];
      foreach ($schema as $sql) { $pdo->exec($sql); }

      // Seed groups (if empty)
      $cnt = (int)$pdo->query("SELECT COUNT(*) c FROM groups")->fetch()['c'];
      if ($cnt === 0) {
        $pdo->exec("INSERT INTO groups (name) VALUES ('Admins'),('Default')");
      }
      // Get Admins group id
      $admin_gid = (int)($pdo->query("SELECT id FROM groups WHERE name='Admins'")->fetch()['id'] ?? 1);

      // Seed icons (if empty)
      $ic = (int)$pdo->query("SELECT COUNT(*) c FROM icons")->fetch()['c'];
      if ($ic === 0) {
        $icons = [
          ['HTTP','HTTP','#1976D2','#FFFFFF'], ['HTTPS','HTTPS','#1565C0','#FFFFFF'],
          ['SSH','SSH','#2E7D32','#FFFFFF'], ['TELNET','TELNET','#455A64','#FFFFFF'],
          ['FTP','FTP','#6A1B9A','#FFFFFF'], ['FTPS','FTPS','#8E24AA','#FFFFFF'],
          ['SFTP','SFTP','#00897B','#FFFFFF'], ['SCP','SCP','#0097A7','#FFFFFF'],
          ['RDP','RDP','#0277BD','#FFFFFF'], ['DB','DB','#5D4037','#FFFFFF'],
          ['WEB','WEB','#3949AB','#FFFFFF'], ['FILES','FILES','#546E7A','#FFFFFF'],
          ['ADMIN','ADMIN','#C62828','#FFFFFF'], ['USER','USER','#7B1FA2','#FFFFFF'],
          ['GROUP','GROUP','#2E7D32','#FFFFFF'],
        ];
        $stmt = $pdo->prepare("INSERT INTO icons (icon_key,label_text,bg_color_hex,fg_color_hex,scope,has_assets) VALUES (?,?,?,?, 'default', 0)");
        foreach($icons as $r){ $stmt->execute([$r[0],$r[1],$r[2],$r[3]]); }
      }

      // Create admin user if username not present
      $exists = $pdo->prepare("SELECT id FROM users WHERE username=?");
      $exists->execute([$admin_username]);
      if ($exists->fetch()) {
        // Update role + ensure token if missing
        $pdo->prepare("UPDATE users SET role='admin', disabled=0 WHERE username=?")->execute([$admin_username]);
        $row = $pdo->prepare("SELECT id, token FROM users WHERE username=?"); $row->execute([$admin_username]);
        $u = $row->fetch();
        $uid = (int)$u['id'];
        if (!$u['token']) {
          $pdo->prepare("UPDATE users SET token=? WHERE id=?")->execute([$admin_token,$uid]);
        } else {
          $admin_token = $u['token']; // keep existing
        }
      } else {
        $ph = password_hash($admin_password, PASSWORD_DEFAULT);
        $stmt = $pdo->prepare("INSERT INTO users (username,password_hash,display_name,email,role,token,disabled,can_group,can_personal)
                               VALUES (?,?,?,?, 'admin', ?, 0, 1, 1)");
        $stmt->execute([$admin_username,$ph,$admin_display,$admin_email,$admin_token]);
        $uid = (int)$pdo->lastInsertId();
        // Put admin in Admins group + full ACL (READ|WRITE|MODIFY|EDIT|DELETE|ARCHIVE = 63)
        $pdo->prepare("INSERT IGNORE INTO user_groups (user_id, group_id) VALUES (?,?)")->execute([$uid,$admin_gid]);
        $pdo->prepare("INSERT INTO user_group_acl (user_id, group_id, perms) VALUES (?,?,63)
                       ON DUPLICATE KEY UPDATE perms=63")->execute([$uid,$admin_gid]);
      }

      // Write config.php
      $config_php = "<?php\nreturn [\n" .
        "  'db' => [\n" .
        "    'host' => ".var_export($db_host,true).",\n" .
        "    'port' => ".var_export($db_port,true).",\n" .
        "    'name' => ".var_export($db_name,true).",\n" .
        "    'user' => ".var_export($app_user,true).",\n" .
        "    'pass' => ".var_export($app_pass,true).",\n" .
        "    'charset' => 'utf8mb4',\n" .
        "  ],\n" .
        "  'mail' => [\n" .
        "    'from' => ".var_export($mail_from,true).",\n" .
        "    'sender' => ".var_export($mail_sender,true).",\n" .
        "    'driver' => 'phpmail' // uses PHP mail()\n" .
        "  ],\n" .
        "];\n";
      file_put_contents($config_path, $config_php);

      // Write api1.php (full API used by the Windows app)
      $api_code = <<<'PHP'
<?php
declare(strict_types=1);
header('Content-Type: application/json; charset=utf-8');

function out($ok, $payload = []) {
  if (!is_array($payload)) $payload = ['detail' => (string)$payload];
  echo json_encode(array_merge(['ok'=>$ok], $payload));
  exit;
}
function err($msg, $detail=null, $code=200){ http_response_code($code); out(false, ['error'=>$msg,'detail'=>$detail]); }

$config = require __DIR__ . '/config.php';
$db = $config['db'];
try {
  $dsn = "mysql:host={$db['host']};port={$db['port']};dbname={$db['name']};charset={$db['charset']}";
  $pdo = new PDO($dsn, $db['user'], $db['pass'], [
    PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
    PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
  ]);
} catch(Throwable $e){ err('Server error', $e->getMessage(), 500); }

$action = $_REQUEST['action'] ?? '';
if ($action === 'sanity') {
  out(true, ['version'=>'api1','time'=>date('c')]);
}

function auth(PDO $pdo): array {
  $tok = $_REQUEST['user_token'] ?? '';
  if (!$tok) err('Auth error','Missing token',401);
  $stmt = $pdo->prepare("SELECT id, username, display_name, email, role, disabled, can_group, can_personal FROM users WHERE token=?");
  $stmt->execute([$tok]);
  $u = $stmt->fetch();
  if (!$u) err('Auth error','Invalid token',401);
  if ((int)$u['disabled'] === 1) err('Auth error','Account disabled',403);
  return $u;
}
function mail_token($to, $name, $token, $from, $sender) {
  if (!$to) return;
  $subj = "Your PentaStarLauncher API token";
  $body = "Hello {$name},\n\nYour API token is:\n\n{$token}\n\nKeep it secure.\n";
  $hdrs = "From: {$sender} <{$from}>\r\n";
  @mail($to, $subj, $body, $hdrs);
}

if ($action === 'me') {
  $u = auth($pdo);
  out(true, ['user'=>$u]);
}

// --- Helpers ---
function uid_from_token(PDO $pdo, string $tok): ?int {
  $s = $pdo->prepare("SELECT id FROM users WHERE token=? AND disabled=0");
  $s->execute([$tok]);
  $r = $s->fetch();
  return $r ? (int)$r['id'] : null;
}
function is_admin(array $me): bool { return ($me['role'] ?? '') === 'admin'; }

// --- Systems fetch (scoped) ---
if ($action === 'systems') {
  $me = auth($pdo);
  $uid = (int)$me['id'];
  // Gather group ids for user
  $gids = $pdo->prepare("SELECT group_id FROM user_groups WHERE user_id=?");
  $gids->execute([$uid]);
  $gid_list = array_map(fn($r)=>(int)$r['group_id'], $gids->fetchAll());
  $gid_csv = $gid_list ? implode(',', $gid_list) : '0';

  // systems visible: system scope; group scope where mapped to any gid; user scope owner = uid; not archived
  $sql = "SELECT s.* FROM systems s
          WHERE s.archived=0 AND (
              s.scope='system'
           OR (s.scope='group' AND EXISTS(SELECT 1 FROM system_groups sg WHERE sg.system_id=s.id AND sg.group_id IN ($gid_csv)))
           OR (s.scope='user' AND s.owner_user_id=:uid)
          )
          ORDER BY s.order_index ASC, s.name ASC";
  $st = $pdo->prepare($sql); $st->execute([':uid'=>$uid]);
  $systems = $st->fetchAll();

  // connections
  $connStmt = $pdo->prepare("SELECT * FROM system_connections WHERE system_id=? ORDER BY order_index, id");
  foreach ($systems as &$s) {
    $connStmt->execute([(int)$s['id']]);
    $s['connections'] = $connStmt->fetchAll();
  }
  out(true, ['systems'=>$systems]);
}

// --- System CRUD ---
function require_rights_on_system(PDO $pdo, array $me, int $sid, string $op): array {
  // Owner or admin can modify; if locked, only admin or owner can modify
  $s = $pdo->prepare("SELECT * FROM systems WHERE id=?");
  $s->execute([$sid]);
  $sys = $s->fetch();
  if (!$sys) err('Not found','system');
  $uid = (int)$me['id'];
  $owner = (int)$sys['owner_user_id'] === $uid;
  if ($op!=='read') {
    if ((int)$sys['locked'] === 1 && !($owner || is_admin($me))) err('Locked','System is locked');
    if (!($owner || is_admin($me))) err('Forbidden','Not owner/admin',403);
  }
  return $sys;
}

if ($action === 'create_system') {
  $me = auth($pdo);
  $name = trim($_POST['name'] ?? '');
  if ($name==='') err('Validation','Name required');
  $scope = $_POST['scope'] ?? 'system';
  $locked = (int)($_POST['locked'] ?? 0);
  $archived = (int)($_POST['archived'] ?? 0);
  $admin_url = $_POST['admin_url'] ?? null;
  $user_url  = $_POST['user_url'] ?? null;
  $group_ids_csv = trim($_POST['group_ids'] ?? '');
  if ($scope==='system' && !is_admin($me)) err('Forbidden','Admin only for scope=system',403);
  if ($scope==='group' && !(int)$me['can_group']) err('Forbidden','No group-create right',403);

  $stmt = $pdo->prepare("INSERT INTO systems (name,admin_url,user_url,icon_id,order_index,created_by,owner_user_id,scope,locked,archived)
                         VALUES (?,?,?,?,0,?,?,?, ?,?)");
  $stmt->execute([$name,$admin_url,$user_url,null,(int)$me['id'],(int)$me['id'],$scope,$locked,$archived]);
  $sid = (int)$pdo->lastInsertId();

  if ($scope==='group' && $group_ids_csv!=='') {
    $vals = array_filter(array_map('intval', explode(',',$group_ids_csv)));
    $ins = $pdo->prepare("INSERT IGNORE INTO system_groups (system_id, group_id) VALUES (?,?)");
    foreach($vals as $gid){ $ins->execute([$sid,$gid]); }
  }
  out(true, ['id'=>$sid]);
}

if ($action === 'update_system') {
  $me = auth($pdo);
  $sid = (int)($_POST['id'] ?? 0);
  if (!$sid) err('Validation','id required');
  $sys = require_rights_on_system($pdo, $me, $sid, 'write');

  $name = trim($_POST['name'] ?? $sys['name']);
  $scope = $_POST['scope'] ?? $sys['scope'];
  $locked = isset($_POST['locked']) ? (int)$_POST['locked'] : (int)$sys['locked'];
  $archived = isset($_POST['archived']) ? (int)$_POST['archived'] : (int)$sys['archived'];
  $admin_url = array_key_exists('admin_url',$_POST) ? $_POST['admin_url'] : $sys['admin_url'];
  $user_url  = array_key_exists('user_url',$_POST)  ? $_POST['user_url']  : $sys['user_url'];
  $group_ids_csv = trim($_POST['group_ids'] ?? '');

  if ($scope==='system' && !is_admin($me)) err('Forbidden','Admin only for scope=system',403);

  $u = $pdo->prepare("UPDATE systems SET name=?, admin_url=?, user_url=?, scope=?, locked=?, archived=? WHERE id=?");
  $u->execute([$name,$admin_url,$user_url,$scope,$locked,$archived,$sid]);

  if ($scope==='group') {
    $pdo->prepare("DELETE FROM system_groups WHERE system_id=?")->execute([$sid]);
    if ($group_ids_csv!=='') {
      $vals = array_filter(array_map('intval', explode(',',$group_ids_csv)));
      $ins = $pdo->prepare("INSERT IGNORE INTO system_groups (system_id, group_id) VALUES (?,?)");
      foreach($vals as $gid){ $ins->execute([$sid,$gid]); }
    }
  } else {
    $pdo->prepare("DELETE FROM system_groups WHERE system_id=?")->execute([$sid]);
  }
  out(true);
}

if ($action === 'delete_system') {
  $me = auth($pdo);
  $sid = (int)($_POST['id'] ?? 0);
  if (!$sid) err('Validation','id required');
  require_rights_on_system($pdo, $me, $sid, 'write');
  $pdo->prepare("DELETE FROM systems WHERE id=?")->execute([$sid]);
  out(true);
}

// --- Connections ---
if ($action === 'list_connections') {
  $me = auth($pdo);
  $sid = (int)($_GET['system_id'] ?? 0);
  if (!$sid) err('Validation','system_id required');
  require_rights_on_system($pdo, $me, $sid, 'read');
  $st = $pdo->prepare("SELECT * FROM system_connections WHERE system_id=? ORDER BY order_index, id");
  $st->execute([$sid]);
  out(true, ['connections'=>$st->fetchAll()]);
}

if ($action === 'create_connection') {
  $me = auth($pdo);
  $sid = (int)($_POST['system_id'] ?? 0);
  if (!$sid) err('Validation','system_id required');
  require_rights_on_system($pdo, $me, $sid, 'write');
  $type = $_POST['type'] ?? 'http';
  $host = $_POST['host'] ?? '';
  $port = (int)($_POST['port'] ?? 0);
  $username = $_POST['username'] ?? null;
  $path = $_POST['path'] ?? null;
  $params = $_POST['params'] ?? null;
  if ($host==='') err('Validation','host required');
  $ins = $pdo->prepare("INSERT INTO system_connections (system_id,type,host,port,username,path,params,icon_id,order_index,created_by)
                        VALUES (?,?,?,?,?,?,?,NULL,0,?)");
  $ins->execute([$sid,$type,$host,$port,$username,$path,$params,(int)$me['id']]);
  out(true, ['id'=>(int)$pdo->lastInsertId()]);
}

if ($action === 'update_connection') {
  $me = auth($pdo);
  $id = (int)($_POST['id'] ?? 0);
  if (!$id) err('Validation','id required');
  $s = $pdo->prepare("SELECT * FROM system_connections WHERE id=?"); $s->execute([$id]);
  $c = $s->fetch(); if (!$c) err('Not found','connection');
  require_rights_on_system($pdo, $me, (int)$c['system_id'], 'write');
  $type = $_POST['type'] ?? $c['type'];
  $host = $_POST['host'] ?? $c['host'];
  $port = (int)($_POST['port'] ?? $c['port']);
  $username = array_key_exists('username',$_POST) ? $_POST['username'] : $c['username'];
  $path = array_key_exists('path',$_POST) ? $_POST['path'] : $c['path'];
  $params = array_key_exists('params',$_POST) ? $_POST['params'] : $c['params'];
  $u = $pdo->prepare("UPDATE system_connections SET type=?,host=?,port=?,username=?,path=?,params=? WHERE id=?");
  $u->execute([$type,$host,$port,$username,$path,$params,$id]);
  out(true);
}

if ($action === 'delete_connection') {
  $me = auth($pdo);
  $id = (int)($_POST['id'] ?? 0);
  if (!$id) err('Validation','id required');
  $s = $pdo->prepare("SELECT * FROM system_connections WHERE id=?"); $s->execute([$id]);
  $c = $s->fetch(); if (!$c) err('Not found','connection');
  require_rights_on_system($pdo, $me, (int)$c['system_id'], 'write');
  $pdo->prepare("DELETE FROM system_connections WHERE id=?")->execute([$id]);
  out(true);
}

// --- Profile + password + token ---
if ($action === 'update_profile') {
  $me = auth($pdo);
  $display = trim($_POST['display_name'] ?? $me['display_name']);
  $email = trim($_POST['email'] ?? ($me['email'] ?? ''));
  $u = $pdo->prepare("UPDATE users SET display_name=?, email=? WHERE id=?");
  $u->execute([$display,$email,(int)$me['id']]);
  out(true);
}
if ($action === 'change_password') {
  $me = auth($pdo);
  $old = $_POST['old_password'] ?? '';
  $new = $_POST['new_password'] ?? '';
  if ($new==='') err('Validation','new password required');
  $s = $pdo->prepare("SELECT password_hash FROM users WHERE id=?"); $s->execute([(int)$me['id']]);
  $ph = $s->fetchColumn();
  if (!$ph || !password_verify($old, $ph)) err('Validation','current password mismatch');
  $nh = password_hash($new, PASSWORD_DEFAULT);
  $pdo->prepare("UPDATE users SET password_hash=? WHERE id=?")->execute([$nh,(int)$me['id']]);
  out(true);
}
if ($action === 'rotate_token') {
  $me = auth($pdo);
  $target = isset($_POST['id']) ? (int)$_POST['id'] : (int)$me['id'];
  if ($target !== (int)$me['id'] && !is_admin($me)) err('Forbidden','Admin only to rotate others',403);
  // Protect: cannot change someone else who is admin if you are not admin; already protected.
  $new = bin2hex(random_bytes(32));
  $pdo->prepare("UPDATE users SET token=? WHERE id=?")->execute([$new,$target]);
  // Email if available
  $s = $pdo->prepare("SELECT email, display_name FROM users WHERE id=?"); $s->execute([$target]);
  $row = $s->fetch();
  $from = $GLOBALS['config']['mail']['from'] ?? null;
  $sender = $GLOBALS['config']['mail']['sender'] ?? 'PentaStar Launcher';
  if (!empty($row['email']) && !empty($from)) { mail_token($row['email'],$row['display_name'] ?? 'User',$new,$from,$sender); }
  out(true, ['new_token'=>$new]);
}

// --- Admin endpoints ---
if (str_starts_with($action,'admin_')) {
  $me = auth($pdo);
  if (!is_admin($me)) err('Forbidden','Admin only',403);
}
if ($action === 'admin_list_groups') {
  $g = $pdo->query("SELECT id,name FROM groups ORDER BY name")->fetchAll();
  out(true, ['groups'=>$g]);
}
if ($action === 'admin_create_group') {
  $name = trim($_POST['name'] ?? '');
  if ($name==='') err('Validation','name required');
  $pdo->prepare("INSERT INTO groups (name) VALUES (?)")->execute([$name]);
  out(true, ['id'=>(int)$pdo->lastInsertId()]);
}
if ($action === 'admin_update_group') {
  $id = (int)($_POST['id'] ?? 0); $name = trim($_POST['name'] ?? '');
  if (!$id || $name==='') err('Validation','id+name required');
  $pdo->prepare("UPDATE groups SET name=? WHERE id=?")->execute([$name,$id]);
  out(true);
}
if ($action === 'admin_delete_group') {
  $id = (int)($_POST['id'] ?? 0); if (!$id) err('Validation','id required');
  $pdo->prepare("DELETE FROM groups WHERE id=?")->execute([$id]);
  out(true);
}
if ($action === 'admin_list_users') {
  $u = $pdo->query("SELECT id,username,display_name,email,role,disabled,can_group,can_personal FROM users ORDER BY username")->fetchAll();
  // group ids for each
  $ug = $pdo->query("SELECT user_id, group_id FROM user_groups")->fetchAll();
  $map = [];
  foreach($ug as $r){ $map[$r['user_id']][] = (int)$r['group_id']; }
  foreach($u as &$row){ $row['group_ids'] = $map[$row['id']] ?? []; }
  out(true, ['users'=>$u]);
}
if ($action === 'admin_create_user') {
  $username = trim($_POST['username'] ?? '');
  $display  = trim($_POST['display_name'] ?? '');
  $email    = trim($_POST['email'] ?? '');
  $password = (string)($_POST['password'] ?? '');
  $can_group = (int)($_POST['can_group'] ?? 1);
  $can_personal = (int)($_POST['can_personal'] ?? 1);
  if ($username==='' || $display==='' || $password==='') err('Validation','username/display/password required');
  // unique username
  $c = $pdo->prepare("SELECT 1 FROM users WHERE username=?"); $c->execute([$username]);
  if ($c->fetch()) err('Validation','username exists');
  $ph = password_hash($password, PASSWORD_DEFAULT);
  $tok = bin2hex(random_bytes(32));
  $pdo->prepare("INSERT INTO users (username,password_hash,display_name,email,role,token,disabled,can_group,can_personal)
                 VALUES (?,?,?,?, 'user', ?, 0, ?, ?)")->execute([$username,$ph,$display,$email,$tok,$can_group,$can_personal]);
  $id = (int)$pdo->lastInsertId();
  // Email token
  $from = $GLOBALS['config']['mail']['from'] ?? null;
  $sender = $GLOBALS['config']['mail']['sender'] ?? 'PentaStar Launcher';
  if (!empty($email) && !empty($from)) { mail_token($email,$display ?: $username,$tok,$from,$sender); }
  out(true, ['id'=>$id,'new_token'=>$tok]);
}
if ($action === 'admin_update_user') {
  $id = (int)($_POST['id'] ?? 0); if (!$id) err('Validation','id required');
  $display = trim($_POST['display_name'] ?? '');
  $email   = trim($_POST['email'] ?? '');
  $can_group = (int)($_POST['can_group'] ?? 1);
  $can_personal = (int)($_POST['can_personal'] ?? 1);
  $pdo->prepare("UPDATE users SET display_name=?, email=?, can_group=?, can_personal=? WHERE id=?")
      ->execute([$display,$email,$can_group,$can_personal,$id]);
  out(true);
}
if ($action === 'admin_delete_user') {
  $id = (int)($_POST['id'] ?? 0); if (!$id) err('Validation','id required');
  // cannot delete admins (safety)
  $r = $pdo->prepare("SELECT role FROM users WHERE id=?"); $r->execute([$id]); $role=$r->fetchColumn();
  if ($role==='admin') err('Forbidden','Cannot delete admin',403);
  $pdo->prepare("DELETE FROM users WHERE id=?")->execute([$id]);
  out(true);
}
if ($action === 'admin_set_user_groups') {
  $id = (int)($_POST['id'] ?? 0); if (!$id) err('Validation','id required');
  $csv = trim($_POST['group_ids'] ?? '');
  $pdo->prepare("DELETE FROM user_groups WHERE user_id=?")->execute([$id]);
  if ($csv!=='') {
    $vals = array_filter(array_map('intval', explode(',',$csv)));
    $ins = $pdo->prepare("INSERT IGNORE INTO user_groups (user_id, group_id) VALUES (?,?)");
    foreach($vals as $gid){ $ins->execute([$id,$gid]); }
  }
  out(true);
}
if ($action === 'admin_get_user_acl') {
  $id = (int)($_GET['id'] ?? 0); if (!$id) err('Validation','id required');
  $rows = $pdo->query("SELECT g.id AS group_id, g.name, COALESCE(uga.perms,0) AS perms
                       FROM groups g
                       LEFT JOIN user_group_acl uga ON (uga.group_id=g.id AND uga.user_id={$id})
                       ORDER BY g.name")->fetchAll();
  out(true, ['acl'=>$rows]);
}
if ($action === 'admin_set_user_acl') {
  $id = (int)($_POST['id'] ?? 0); if (!$id) err('Validation','id required');
  $json = $_POST['acl'] ?? '[]';
  $arr = json_decode($json, true);
  if (!is_array($arr)) err('Validation','bad acl json');
  $pdo->prepare("DELETE FROM user_group_acl WHERE user_id=?")->execute([$id]);
  $ins = $pdo->prepare("INSERT INTO user_group_acl (user_id, group_id, perms) VALUES (?,?,?)");
  foreach($arr as $row){
    $gid = (int)($row['group_id'] ?? 0);
    $perms = (int)($row['perms'] ?? 0);
    if ($gid>0) $ins->execute([$id,$gid,$perms]);
  }
  out(true);
}
if ($action === 'admin_disable_user') {
  $me = auth($pdo);
  $id = (int)($_POST['id'] ?? 0); $disabled = (int)($_POST['disabled'] ?? 0);
  if (!$id) err('Validation','id required');
  // prevent disabling self if admin
  if ($id === (int)$me['id']) err('Forbidden','Cannot disable your own account',403);
  // prevent disabling another admin
  $r = $pdo->prepare("SELECT role FROM users WHERE id=?"); $r->execute([$id]); $role=$r->fetchColumn();
  if ($role==='admin') err('Forbidden','Cannot disable an admin',403);
  $pdo->prepare("UPDATE users SET disabled=? WHERE id=?")->execute([$disabled,$id]);
  out(true);
}
if ($action === 'admin_set_role') {
  $me = auth($pdo);
  $id = (int)($_POST['id'] ?? 0); $role = $_POST['role'] ?? 'user';
  if (!$id) err('Validation','id required');
  if ($id === (int)$me['id'] && $role!=='admin') err('Forbidden','Cannot remove your own admin',403);
  if (!in_array($role,['user','admin'],true)) err('Validation','bad role');
  // If demoting an admin, ensure at least one admin remains
  $r = $pdo->prepare("SELECT role FROM users WHERE id=?"); $r->execute([$id]); $old=$r->fetchColumn();
  if ($old==='admin' && $role==='user') {
    $c = (int)$pdo->query("SELECT COUNT(*) c FROM users WHERE role='admin' AND disabled=0")->fetch()['c'];
    if ($c <= 1) err('Forbidden','Cannot demote the last admin',403);
  }
  $pdo->prepare("UPDATE users SET role=? WHERE id=?")->execute([$role,$id]);
  out(true);
}

err('Unknown action', $action, 404);
PHP;
      file_put_contents($api_path, $api_code);

      $okmsg = "Installation complete.";
      $results['app_db_user'] = $app_user;
      $results['app_db_pass'] = $app_pass;
      $results['admin_user']  = $admin_username;
      $results['admin_token'] = $admin_token;
      $results['db_name']     = $db_name;

    } catch (Throwable $e) {
      $errors[] = "Install failed: " . $e->getMessage();
    }
  }
}

?>
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>PentaStarLauncher Installer</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root {
  --bg:#0f172a; --panel:#111827; --text:#e5e7eb; --muted:#9ca3af; --accent:#2563eb; --ok:#16a34a; --err:#dc2626;
  --input:#0b1220; --border:#233148;
}
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--text); font:14px/1.4 system-ui,Segoe UI,Roboto,Ubuntu,Arial,sans-serif; }
.container { max-width: 980px; margin: 40px auto; padding: 0 16px; }
.card { background: var(--panel); border:1px solid var(--border); border-radius: 12px; padding: 20px; }
h1 { font-size: 20px; margin: 0 0 16px; }
h2 { font-size: 16px; margin: 16px 0 8px; color: var(--muted); }
.row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
label { display:block; font-weight:600; margin:8px 0 6px; }
input[type=text],input[type=password],input[type=email],input[type=number]{
  width:100%; padding:10px; border-radius:8px; border:1px solid var(--border); background:var(--input); color:var(--text);
}
.btn { display:inline-block; background:var(--accent); color:white; padding:10px 14px; border:none; border-radius:8px; font-weight:700; cursor:pointer; }
.btn:disabled{ opacity:.6; cursor:not-allowed; }
.small { color: var(--muted); font-size: 12px; }
.kv { display:grid; grid-template-columns: 220px 1fr 120px; gap:10px; align-items:center; margin:10px 0; }
code, .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.alert { padding:10px 12px; border-radius:8px; margin:12px 0; }
.alert.ok { background: rgba(22,163,74,.15); border:1px solid rgba(22,163,74,.35); }
.alert.err{ background: rgba(220,38,38,.12); border:1px solid rgba(220,38,38,.35); }
.copy { background:#0b1220; border:1px dashed var(--border); padding:8px 10px; border-radius:8px; display:flex; gap:10px; align-items:center; }
.copy input { border:none; background:transparent; color:var(--text); width:100%; }
.copy button { background:var(--accent); color:white; border:none; padding:6px 10px; border-radius:6px; cursor:pointer; }
hr { border:0; border-top:1px solid var(--border); margin:18px 0; }
footer { text-align:center; color:var(--muted); margin: 24px 0; }
</style>
</head>
<body>
<div class="container">
  <div class="card">
    <h1>PentaStarLauncher – Web Installer</h1>
    <?php if ($okmsg): ?>
      <div class="alert ok"><?=h($okmsg)?></div>
      <h2>Configuration</h2>
      <div class="kv"><div>Database name</div><div class="mono"><?=h($results['db_name'])?></div><div></div></div>
      <div class="kv"><div>App DB user</div><div class="mono"><?=h($results['app_db_user'])?></div>
        <div class="copy"><input id="dbpass" readonly value="<?=h($results['app_db_pass'])?>"><button onclick="copy('dbpass')">Copy</button></div>
      </div>
      <div class="kv"><div>Admin username</div><div class="mono"><?=h($results['admin_user'])?></div><div></div></div>
      <div class="kv"><div>Admin token</div>
        <div class="copy"><input id="admtok" readonly value="<?=h($results['admin_token'])?>"></div>
        <div><button class="btn" onclick="copy('admtok')">Copy token</button></div>
      </div>
      <hr>
      <p class="small">Files written: <span class="mono">config.php</span> and <span class="mono">api1.php</span> in this directory.</p>
      <p><a class="btn" href="api1.php?action=sanity" target="_blank">Test API &raquo;</a></p>
    <?php else: ?>
      <?php if ($errors): ?>
        <div class="alert err">
          <strong>Fix these errors:</strong>
          <ul>
          <?php foreach($errors as $e): ?><li><?=h($e)?></li><?php endforeach; ?>
          </ul>
        </div>
      <?php endif; ?>

      <form method="post">
        <h2>MariaDB (root) connection</h2>
        <div class="row">
          <div>
            <label>Host</label>
            <input type="text" name="db_host" value="<?=h($_POST['db_host'] ?? 'localhost')?>">
          </div>
          <div>
            <label>Port</label>
            <input type="number" name="db_port" value="<?=h($_POST['db_port'] ?? '3306')?>">
          </div>
        </div>
        <div class="row">
          <div>
            <label>Root username</label>
            <input type="text" name="root_user" value="<?=h($_POST['root_user'] ?? 'root')?>">
          </div>
          <div>
            <label>Root password</label>
            <input type="password" name="root_pass" value="<?=h($_POST['root_pass'] ?? '')?>">
          </div>
        </div>

        <h2>Application database</h2>
        <div class="row">
          <div>
            <label>Database name</label>
            <input type="text" name="db_name" value="<?=h($_POST['db_name'] ?? 'taskapp1')?>">
          </div>
          <div>
            <label>App DB user</label>
            <input type="text" name="app_user" value="<?=h($_POST['app_user'] ?? 'taskapp1_app')?>">
          </div>
        </div>
        <div>
          <label>App DB password <span class="small">(leave blank to auto-generate)</span></label>
          <input type="text" name="app_pass" value="<?=h($_POST['app_pass'] ?? '')?>">
        </div>

        <h2>Initial admin user</h2>
        <div class="row">
          <div>
            <label>Username</label>
            <input type="text" name="admin_username" value="<?=h($_POST['admin_username'] ?? 'admin')?>">
          </div>
          <div>
            <label>Display name</label>
            <input type="text" name="admin_display" value="<?=h($_POST['admin_display'] ?? 'Administrator')?>">
          </div>
        </div>
        <div class="row">
          <div>
            <label>Email <span class="small">(optional; used to email tokens)</span></label>
            <input type="email" name="admin_email" value="<?=h($_POST['admin_email'] ?? '')?>">
          </div>
          <div>
            <label>Password <span class="small">(leave blank to auto-generate)</span></label>
            <input type="text" name="admin_password" value="<?=h($_POST['admin_password'] ?? '')?>">
          </div>
        </div>

        <h2>Mail settings</h2>
        <div class="row">
          <div>
            <label>From address</label>
            <input type="text" name="mail_from" value="<?=h($_POST['mail_from'] ?? ('no-reply@'.($_SERVER['HTTP_HOST'] ?? 'localhost')))?>">
          </div>
          <div>
            <label>Sender name</label>
            <input type="text" name="mail_sender" value="<?=h($_POST['mail_sender'] ?? 'PentaStar Launcher')?>">
          </div>
        </div>

        <div style="margin-top:16px">
          <button class="btn" type="submit">Install</button>
        </div>
      </form>
    <?php endif; ?>
  </div>
  <footer>&copy; <?=date('Y')?> PentaStar Studios</footer>
</div>
<script>
function copy(id){ const el=document.getElementById(id); el.select(); el.setSelectionRange(0,99999); document.execCommand('copy'); }
</script>
</body>
</html>
