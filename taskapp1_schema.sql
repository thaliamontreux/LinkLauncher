-- taskapp1_schema.sql
-- PentaStarLauncher database schema (MariaDB 10.4+ / MySQL 8+)
-- Creates DB, tables, constraints, indexes, and seeds default groups & icons.

-- =========================================================
-- 0) Create database
-- =========================================================
CREATE DATABASE IF NOT EXISTS `taskapp1`
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE `taskapp1`;

-- Safety for clean (re)runs
SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='STRICT_ALL_TABLES,NO_AUTO_VALUE_ON_ZERO';
SET FOREIGN_KEY_CHECKS=0;

-- =========================================================
-- 1) Core tables
-- =========================================================

-- Users
DROP TABLE IF EXISTS `users`;
CREATE TABLE `users` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `username` VARCHAR(191) NOT NULL UNIQUE,
  `password_hash` VARCHAR(255) NOT NULL,
  `display_name` VARCHAR(255) NOT NULL,
  `email` VARCHAR(255) DEFAULT NULL,
  `role` ENUM('user','admin') NOT NULL DEFAULT 'user',
  `token` CHAR(64) NOT NULL UNIQUE,
  `disabled` TINYINT(1) NOT NULL DEFAULT 0,
  `can_group` TINYINT(1) NOT NULL DEFAULT 1,
  `can_personal` TINYINT(1) NOT NULL DEFAULT 1,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Groups
DROP TABLE IF EXISTS `groups`;
CREATE TABLE `groups` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `name` VARCHAR(191) NOT NULL UNIQUE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- User <-> Group membership
DROP TABLE IF EXISTS `user_groups`;
CREATE TABLE `user_groups` (
  `user_id` INT NOT NULL,
  `group_id` INT NOT NULL,
  PRIMARY KEY (`user_id`, `group_id`),
  CONSTRAINT `fk_ug_user`  FOREIGN KEY (`user_id`)  REFERENCES `users`(`id`)  ON DELETE CASCADE,
  CONSTRAINT `fk_ug_group` FOREIGN KEY (`group_id`) REFERENCES `groups`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Per-user ACL for a given group (bitmask: READ=1, WRITE=2, MODIFY=4, EDIT=8, DELETE=16, ARCHIVE=32)
DROP TABLE IF EXISTS `user_group_acl`;
CREATE TABLE `user_group_acl` (
  `user_id`  INT NOT NULL,
  `group_id` INT NOT NULL,
  `perms`    INT UNSIGNED NOT NULL DEFAULT 0,
  PRIMARY KEY (`user_id`, `group_id`),
  CONSTRAINT `fk_uga_user`  FOREIGN KEY (`user_id`)  REFERENCES `users`(`id`)  ON DELETE CASCADE,
  CONSTRAINT `fk_uga_group` FOREIGN KEY (`group_id`) REFERENCES `groups`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Icons (catalog)
DROP TABLE IF EXISTS `icons`;
CREATE TABLE `icons` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `icon_key` VARCHAR(64) NOT NULL UNIQUE,
  `label_text` VARCHAR(16) NOT NULL,
  `bg_color_hex` VARCHAR(7) NOT NULL DEFAULT '#1976D2',
  `fg_color_hex` VARCHAR(7) NOT NULL DEFAULT '#FFFFFF',
  `scope` ENUM('default','custom') NOT NULL DEFAULT 'default',
  `has_assets` TINYINT(1) NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Icon binary assets (optional; for future inline PNGs)
DROP TABLE IF EXISTS `icon_assets`;
CREATE TABLE `icon_assets` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `icon_id` INT NOT NULL,
  `width` INT NOT NULL,
  `height` INT NOT NULL,
  `format` ENUM('png') NOT NULL DEFAULT 'png',
  `mime_type` VARCHAR(64) NOT NULL DEFAULT 'image/png',
  `sha256` CHAR(64) NOT NULL,
  `bytes` LONGBLOB NOT NULL,
  UNIQUE KEY `uniq_icon_size` (`icon_id`, `width`, `height`, `format`),
  CONSTRAINT `fk_iconasset_icon` FOREIGN KEY (`icon_id`) REFERENCES `icons`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Systems (launchers groupings)
DROP TABLE IF EXISTS `systems`;
CREATE TABLE `systems` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `name` VARCHAR(255) NOT NULL,
  `admin_url` TEXT NULL,
  `user_url`  TEXT NULL,
  `icon_id` INT NULL,
  `order_index` INT NOT NULL DEFAULT 0,
  `created_by` INT NOT NULL,
  `owner_user_id` INT NOT NULL,
  `scope` ENUM('system','group','user') NOT NULL DEFAULT 'system',
  `locked` TINYINT(1) NOT NULL DEFAULT 0,
  `archived` TINYINT(1) NOT NULL DEFAULT 0,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY `idx_system_icon` (`icon_id`),
  KEY `idx_system_owner` (`owner_user_id`),
  CONSTRAINT `fk_system_owner` FOREIGN KEY (`owner_user_id`) REFERENCES `users`(`id`) ON DELETE RESTRICT,
  CONSTRAINT `fk_system_icon`  FOREIGN KEY (`icon_id`)       REFERENCES `icons`(`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- System <-> Group mapping (for scope='group')
DROP TABLE IF EXISTS `system_groups`;
CREATE TABLE `system_groups` (
  `system_id` INT NOT NULL,
  `group_id` INT NOT NULL,
  PRIMARY KEY (`system_id`, `group_id`),
  KEY `idx_sysgrp_group` (`group_id`),
  CONSTRAINT `fk_sysgrp_sys` FOREIGN KEY (`system_id`) REFERENCES `systems`(`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_sysgrp_grp` FOREIGN KEY (`group_id`) REFERENCES `groups`(`id`)  ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Connections per system
DROP TABLE IF EXISTS `system_connections`;
CREATE TABLE `system_connections` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `system_id` INT NOT NULL,
  `type` ENUM('http','https','ssh','telnet','ftp','ftps','sftp','scp','rdp') NOT NULL,
  `host` VARCHAR(255) NOT NULL,
  `port` INT NOT NULL,
  `username` VARCHAR(255) NULL,
  `path` VARCHAR(1024) NULL,
  `params` VARCHAR(1024) NULL,
  `icon_id` INT NULL,
  `order_index` INT NOT NULL DEFAULT 0,
  `created_by` INT NOT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY `idx_conn_sys` (`system_id`),
  KEY `idx_conn_type` (`type`),
  CONSTRAINT `fk_conn_sys`  FOREIGN KEY (`system_id`) REFERENCES `systems`(`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_conn_icon` FOREIGN KEY (`icon_id`)   REFERENCES `icons`(`id`)   ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =========================================================
-- 2) Seeds (idempotent)
-- =========================================================

-- Groups
INSERT IGNORE INTO `groups` (`id`,`name`) VALUES
  (1,'Admins'),
  (2,'Default');

-- Default icon catalog (15 items)
INSERT IGNORE INTO `icons` (`icon_key`,`label_text`,`bg_color_hex`,`fg_color_hex`,`scope`,`has_assets`) VALUES
  ('HTTP','HTTP','#1976D2','#FFFFFF','default',0),
  ('HTTPS','HTTPS','#1565C0','#FFFFFF','default',0),
  ('SSH','SSH','#2E7D32','#FFFFFF','default',0),
  ('TELNET','TELNET','#455A64','#FFFFFF','default',0),
  ('FTP','FTP','#6A1B9A','#FFFFFF','default',0),
  ('FTPS','FTPS','#8E24AA','#FFFFFF','default',0),
  ('SFTP','SFTP','#00897B','#FFFFFF','default',0),
  ('SCP','SCP','#0097A7','#FFFFFF','default',0),
  ('RDP','RDP','#0277BD','#FFFFFF','default',0),
  ('DB','DB','#5D4037','#FFFFFF','default',0),
  ('WEB','WEB','#3949AB','#FFFFFF','default',0),
  ('FILES','FILES','#546E7A','#FFFFFF','default',0),
  ('ADMIN','ADMIN','#C62828','#FFFFFF','default',0),
  ('USER','USER','#7B1FA2','#FFFFFF','default',0),
  ('GROUP','GROUP','#2E7D32','#FFFFFF','default',0);

-- =========================================================
-- 3) Optional admin bootstrap (manual step)
--    NOTE: The API expects PHP's password_hash() compatible hashes (e.g., bcrypt/argon2).
--    Replace @admin_password_hash with a bcrypt hash (starts with `$2y$`), or run the web installer to handle it.
--    This block is safe to re-run; ON DUPLICATE keeps the existing token.
-- =========================================================
/*
SET @admin_username = 'admin';
SET @admin_display  = 'Administrator';
SET @admin_email    = 'admin@example.com';
SET @admin_password_hash = '$2y$10$REPLACE_WITH_BCRYPT_HASH_FROM_PHP_PASSWORD_HASH';
SET @admin_token    = UPPER(HEX(RANDOM_BYTES(32)));

INSERT INTO `users` (username,password_hash,display_name,email,role,token,disabled,can_group,can_personal)
VALUES (@admin_username,@admin_password_hash,@admin_display,@admin_email,'admin',@admin_token,0,1,1)
ON DUPLICATE KEY UPDATE role=VALUES(role);

-- Ensure admin is in Admins group and has full ACL (READ|WRITE|MODIFY|EDIT|DELETE|ARCHIVE = 63)
INSERT IGNORE INTO `user_groups` (user_id, group_id)
SELECT u.id, g.id FROM users u JOIN groups g ON g.name='Admins' WHERE u.username=@admin_username;

INSERT INTO `user_group_acl` (user_id, group_id, perms)
SELECT u.id, g.id, 63 FROM users u JOIN groups g ON g.name='Admins' WHERE u.username=@admin_username
ON DUPLICATE KEY UPDATE perms=VALUES(perms);

-- Show the token we just created (for convenience)
SELECT token FROM users WHERE username=@admin_username;
*/

-- =========================================================
-- 4) Finalize
-- =========================================================
SET FOREIGN_KEY_CHECKS=1;
SET SQL_MODE=@OLD_SQL_MODE;

