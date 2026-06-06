from __future__ import annotations
import logging
import logging.handlers
import os
import smtplib
import socket
import time
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Any

_logger: logging.Logger | None = None


def setup_logger(config: dict) -> logging.Logger:
    """
    Configure the root backup logger.
    - File handler: TimedRotatingFileHandler (midnight, keep 30 days)
    - Stream handler: console
    - Format: [YYYY-MM-DD HH:MM:SS UTC] [LEVEL] [name] message
    Returns the configured logger.
    """
    global _logger
    log_path = config.get('log_path', 'logs/backup.log')
    os.makedirs(os.path.dirname(log_path) or '.', exist_ok=True)

    fmt = logging.Formatter(
        '[%(asctime)s UTC] [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    fmt.converter = time.gmtime  # Force UTC

    file_handler = logging.handlers.TimedRotatingFileHandler(
        log_path, when='midnight', backupCount=30, encoding='utf-8'
    )
    file_handler.setFormatter(fmt)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)

    logger = logging.getLogger('backup')
    logger.setLevel(logging.DEBUG)
    # Avoid duplicate handlers if setup_logger is called multiple times
    logger.handlers.clear()
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    _logger = logger
    return logger


def _get_logger() -> logging.Logger:
    """Return the module logger (falls back to root logger if setup_logger not called)."""
    return _logger if _logger is not None else logging.getLogger('backup')


def _try_send_email(subject: str, body: str, config: dict) -> None:
    try:
        send_email(subject, body, config)
    except Exception as e:
        _get_logger().warning('Email send failed (non-fatal): %s', e)


def send_alert(level: str, message: str, config: dict, job_name: str = '') -> None:
    """
    Log the alert and, for error/critical levels, attempt to send email.
    level: 'info' | 'warn' | 'error' | 'critical'
    Email failures are caught and logged — never propagate.
    """
    log = _get_logger()
    tag = f'[{job_name}] ' if job_name else ''
    full_msg = f'{tag}{message}'

    if level == 'info':
        log.info(full_msg)
    elif level == 'warn':
        log.warning(full_msg)
    elif level == 'error':
        log.error(full_msg)
        _try_send_email(f'[BACKUP ERROR] {job_name or "system"}', full_msg, config)
    elif level == 'critical':
        log.critical(full_msg)
        _try_send_email(f'[BACKUP CRITICAL] {job_name or "system"}', full_msg, config)


def send_email(subject: str, body: str, config: dict) -> bool:
    """
    Send email via SMTP. Returns True on success, False on failure.
    Uses config['email'] dict with: smtp_host, smtp_port, smtp_tls, from, to, username, password.
    If config['email']['enabled'] is False, skip and return True.
    """
    email_cfg: dict = config.get('email', {})

    if not email_cfg.get('enabled', False):
        return True

    smtp_host: str = email_cfg.get('smtp_host', '')
    smtp_port: int = int(email_cfg.get('smtp_port', 587))
    smtp_tls: bool = bool(email_cfg.get('smtp_tls', True))
    from_addr: str = email_cfg.get('from', '')
    to_addr: str = email_cfg.get('to', '')
    username: str = email_cfg.get('username', '')
    password: str = email_cfg.get('password', '')

    try:
        msg = MIMEMultipart()
        msg['Subject'] = subject
        msg['From'] = from_addr
        msg['To'] = to_addr
        msg['Date'] = datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0000')

        msg.attach(MIMEText(body, 'plain', 'utf-8'))

        if smtp_tls:
            server = smtplib.SMTP(smtp_host, smtp_port, timeout=10)
            server.ehlo()
            server.starttls()
            server.ehlo()
        else:
            server = smtplib.SMTP(smtp_host, smtp_port, timeout=10)
            server.ehlo()

        if username and password:
            server.login(username, password)

        server.sendmail(from_addr, [to_addr], msg.as_string())
        server.quit()
        _get_logger().info('Alert email sent: %s', subject)
        return True

    except (smtplib.SMTPException, socket.error, OSError, TimeoutError) as e:
        _get_logger().warning('Failed to send email "%s": %s', subject, e)
        return False


# ---------------------------------------------------------------------------
# Alert template functions
# ---------------------------------------------------------------------------

def alert_backup_failed(job_name: str, error_msg: str, config: dict) -> None:
    send_alert('error', f'Backup FAILED: {error_msg}', config, job_name)


def alert_md5_mismatch(job_name: str, expected: str, actual: str, config: dict) -> None:
    send_alert('error', f'MD5 MISMATCH: expected={expected}, actual={actual}', config, job_name)


def alert_chain_broken(job_name: str, missing_files: list, config: dict) -> None:
    send_alert('error', f'Chain BROKEN: missing files {missing_files}', config, job_name)


def alert_disk_full(disk_path: str, free_gb: float, config: dict) -> None:
    send_alert('critical', f'Disk FULL: {disk_path} only {free_gb:.1f}GB free', config)


def alert_monthly_drill_reminder(config: dict) -> None:
    """V1: Send monthly reminder to manually run recovery drill."""
    send_alert('warn', 'Monthly recovery drill is due. Please manually run restore test.', config)
