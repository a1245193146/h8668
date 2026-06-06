'use strict';

let charts = {};
let currentTheme = document.documentElement.getAttribute('data-theme') || 'dark';
let refreshTimer = null;

// ─── Theme ──────────────────────────────────────────────────────────────────
function toggleTheme() {
  currentTheme = currentTheme === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', currentTheme);
  document.getElementById('theme-btn').textContent = currentTheme === 'dark' ? '🌙' : '☀️';
  // Redraw charts with new theme colors
  loadData();
}

// ─── Color palette ───────────────────────────────────────────────────────────
function getColors() {
  const isDark = currentTheme === 'dark';
  return {
    blue:   '#0a84ff',
    green:  '#30d158',
    red:    '#ff453a',
    orange: '#ff9f0a',
    purple: '#bf5af2',
    teal:   '#5ac8fa',
    text:   isDark ? '#f5f5f7' : '#1c1c1e',
    subtext: isDark ? 'rgba(255,255,255,0.5)' : 'rgba(0,0,0,0.4)',
    grid:   isDark ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.08)',
    surface: isDark ? 'rgba(255,255,255,0.05)' : 'rgba(0,0,0,0.04)',
  };
}

// ─── Data Loading ─────────────────────────────────────────────────────────────
async function loadData() {
  try {
    const resp = await fetch('backup_status.json?' + Date.now());
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const data = await resp.json();
    renderAll(data);
  } catch (e) {
    console.warn('Failed to load backup_status.json:', e);
    renderAll({ disks: [], jobs: [], alerts: [] });
  }
}

function renderAll(data) {
  renderDiskChart(data.disks || []);
  renderTimelineChart(data.jobs || []);
  renderChainStatus(data.jobs || []);
  renderAlertList(data.alerts || []);
  
  const lastUpdated = document.getElementById('last-updated');
  if (lastUpdated && data.last_updated) {
    const d = new Date(data.last_updated);
    lastUpdated.textContent = '最后更新: ' + d.toLocaleString('zh-CN');
  }
}

// ─── Disk Doughnut Chart ──────────────────────────────────────────────────────
function renderDiskChart(disks) {
  const container = document.getElementById('disk-section');
  const canvas = document.getElementById('disk-chart');
  const colors = getColors();
  
  if (!disks.length) {
    canvas.style.display = 'none';
    showEmpty(container, '暂无磁盘数据');
    return;
  }
  
  canvas.style.display = 'block';
  removeEmpty(container);
  
  if (charts.disk) { charts.disk.destroy(); }
  
  const palette = [colors.blue, colors.green, colors.orange, colors.purple, colors.teal];
  const diskData = disks.map((d, i) => ({
    label: d.path,
    used: d.used_gb,
    free: d.free_gb,
    color: palette[i % palette.length],
  }));
  
  charts.disk = new Chart(canvas, {
    type: 'doughnut',
    data: {
      labels: diskData.flatMap(d => [d.label + ' 已用', d.label + ' 可用']),
      datasets: [{
        data: diskData.flatMap(d => [d.used, d.free]),
        backgroundColor: diskData.flatMap(d => [d.color, d.color + '33']),
        borderWidth: 0,
        hoverOffset: 4,
      }]
    },
    options: {
      responsive: true,
      plugins: {
        legend: { 
          position: 'bottom',
          labels: { color: colors.text, padding: 12, font: { size: 11 } }
        },
        tooltip: {
          callbacks: {
            label: ctx => `${ctx.label}: ${ctx.parsed.toFixed(1)} GB`
          }
        }
      },
      cutout: '65%',
    }
  });
}

// ─── Timeline Bar Chart ───────────────────────────────────────────────────────
function renderTimelineChart(jobs) {
  const container = document.getElementById('timeline-section');
  const canvas = document.getElementById('timeline-chart');
  const colors = getColors();
  
  if (!jobs.length) {
    canvas.style.display = 'none';
    showEmpty(container, '暂无备份任务');
    return;
  }
  
  canvas.style.display = 'block';
  removeEmpty(container);
  
  if (charts.timeline) { charts.timeline.destroy(); }
  
  const jobNames = jobs.map(j => j.name);
  const successData = jobs.map(j => j.last_result === 'success' ? j.file_size_mb || 1 : 0);
  const failData = jobs.map(j => j.last_result === 'failed' ? 1 : 0);
  const skippedData = jobs.map(j => j.last_result === 'skipped' ? 1 : 0);
  
  charts.timeline = new Chart(canvas, {
    type: 'bar',
    data: {
      labels: jobNames,
      datasets: [
        { label: '成功', data: successData, backgroundColor: colors.green + 'cc' },
        { label: '失败', data: failData, backgroundColor: colors.red + 'cc' },
        { label: '跳过', data: skippedData, backgroundColor: colors.orange + 'cc' },
      ]
    },
    options: {
      responsive: true,
      scales: {
        x: { 
          stacked: true,
          ticks: { color: colors.subtext },
          grid: { color: colors.grid }
        },
        y: { 
          stacked: true,
          display: false
        }
      },
      plugins: {
        legend: { labels: { color: colors.text } }
      }
    }
  });
}

// ─── Chain Status Cards ───────────────────────────────────────────────────────
function renderChainStatus(jobs) {
  const container = document.getElementById('chain-status-list');
  container.innerHTML = '';
  
  if (!jobs.length) {
    showEmpty(document.getElementById('chain-section'), '暂无备份任务');
    return;
  }
  removeEmpty(document.getElementById('chain-section'));
  
  jobs.forEach((job, idx) => {
    const intact = job.chain_status === 'intact';
    const card = document.createElement('div');
    card.className = 'chain-item';
    card.style.cssText = `
      display:flex; align-items:center; gap:12px; padding:12px 16px;
      border-radius:12px; margin-bottom:8px;
      background: rgba(${intact ? '48,209,88' : '255,69,58'},0.1);
      animation: fadeInUp 0.3s ease both;
      animation-delay: ${idx * 60}ms;
    `;
    
    const dot = document.createElement('span');
    dot.className = intact ? 'status-dot status-ok' : 'status-dot status-error';
    
    const nameEl = document.createElement('span');
    nameEl.style.cssText = 'flex:1; font-weight:500; font-size:14px;';
    nameEl.textContent = job.name;
    
    const typeEl = document.createElement('span');
    typeEl.style.cssText = 'font-size:11px; opacity:0.5; text-transform:uppercase;';
    typeEl.textContent = job.type;
    
    const statusEl = document.createElement('span');
    statusEl.style.cssText = `font-size:12px; font-weight:600; color:${intact ? '#30d158' : '#ff453a'};`;
    statusEl.textContent = intact ? '✓ 完整' : '⚠ 断裂';
    
    card.append(dot, nameEl, typeEl, statusEl);
    container.appendChild(card);
  });
}

// ─── Alert List ───────────────────────────────────────────────────────────────
function renderAlertList(alerts) {
  const list = document.getElementById('alert-list');
  list.innerHTML = '';
  
  if (!alerts.length) {
    showEmpty(document.getElementById('alert-section'), '暂无告警');
    return;
  }
  removeEmpty(document.getElementById('alert-section'));
  
  // Newest first, cap at 20
  const sorted = [...alerts].sort((a, b) => b.time.localeCompare(a.time)).slice(0, 20);
  
  const levelColors = { info: '#0a84ff', warn: '#ff9f0a', error: '#ff453a', critical: '#bf5af2' };
  
  sorted.forEach((alert, idx) => {
    const li = document.createElement('li');
    li.style.cssText = `
      padding:10px 14px; border-radius:10px; margin-bottom:6px;
      border-left:3px solid ${levelColors[alert.level] || '#666'};
      background: rgba(255,255,255,0.03);
      animation: fadeInUp 0.3s ease both;
      animation-delay: ${idx * 40}ms;
    `;
    
    const time = new Date(alert.time).toLocaleString('zh-CN', { hour12: false });
    li.innerHTML = `
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:4px;">
        <span style="font-size:10px;font-weight:700;color:${levelColors[alert.level]};text-transform:uppercase;">${alert.level}</span>
        <span style="font-size:11px;opacity:0.45;">${time}</span>
      </div>
      <div style="font-size:13px;opacity:0.85;">${escapeHtml(alert.message)}</div>
    `;
    list.appendChild(li);
  });
}

// ─── Helpers ──────────────────────────────────────────────────────────────────
function escapeHtml(str) {
  return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function showEmpty(container, msg) {
  if (!container.querySelector('.empty-state')) {
    const el = document.createElement('p');
    el.className = 'empty-state';
    el.style.cssText = 'text-align:center;opacity:0.4;padding:32px;font-size:14px;';
    el.textContent = msg;
    container.appendChild(el);
  }
}

function removeEmpty(container) {
  const el = container.querySelector('.empty-state');
  if (el) el.remove();
}

// ─── Init & Auto-refresh ─────────────────────────────────────────────────────
function init() {
  loadData();
  clearInterval(refreshTimer);
  refreshTimer = setInterval(loadData, 60000);
}
