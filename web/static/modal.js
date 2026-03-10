/* Task detail modal with SSE live status updates */
(function () {
  'use strict';

  var backdrop = document.getElementById('task-modal-backdrop');
  var modalBody = document.getElementById('task-modal-body');
  var closeBtn = document.getElementById('task-modal-close');
  var activeEventSource = null;

  function statusClass(status) {
    return 'badge badge-' + status;
  }

  function statusLabel(status) {
    return status.toUpperCase().replace(/_/g, ' ');
  }

  function escapeHtml(str) {
    if (str == null) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function renderModal(data) {
    var task = data.task || {};
    var specs = data.specs || [];
    var executions = data.executions || [];
    var deps = data.dependencies || [];
    var qaFailure = data.qa_failure || null;
    var projectName = data.project_name || task.project_id || 'unknown';

    var currentSpec = null;
    if (task.current_spec_id) {
      for (var i = 0; i < specs.length; i++) {
        if (specs[i].id === task.current_spec_id) {
          currentSpec = specs[i];
          break;
        }
      }
    }

    var html = '<h1>' + escapeHtml(task.title) + '</h1>';
    html += '<dl>';
    html += '<dt>ID</dt><dd>' + escapeHtml(task.id) + '</dd>';
    html += '<dt>Status</dt><dd><span id="modal-status-badge" class="' + statusClass(task.status) + '">' + statusLabel(task.status) + '</span></dd>';
    html += '<dt>Project</dt><dd><a href="/projects/' + escapeHtml(task.project_id) + '">' + escapeHtml(projectName) + '</a></dd>';
    html += '<dt>Refinements</dt><dd>' + escapeHtml(String(task.refinement_count || 0)) + '</dd>';
    html += '</dl>';

    if (deps.length > 0) {
      html += '<h2>Dependencies</h2><ul>';
      for (var d = 0; d < deps.length; d++) {
        var dep = deps[d];
        html += '<li><a href="/tasks/' + escapeHtml(dep) + '">' + escapeHtml(dep).substring(0, 8) + '</a></li>';
      }
      html += '</ul>';
    }

    html += '<h2>Current Spec</h2>';
    if (currentSpec) {
      html += '<p><a href="/tasks/' + escapeHtml(task.id) + '/spec/new">Assign new spec</a></p>';
      html += '<pre>' + escapeHtml(currentSpec.content) + '</pre>';
    } else {
      html += '<p>No spec assigned. <a href="/tasks/' + escapeHtml(task.id) + '/spec/new">Assign a spec</a></p>';
    }

    html += '<h2>Spec History</h2>';
    if (specs.length > 0) {
      html += '<table><thead><tr><th>Spec ID</th><th>Created At</th></tr></thead><tbody>';
      for (var s = 0; s < specs.length; s++) {
        html += '<tr><td><a href="/specs/' + escapeHtml(specs[s].id) + '">' + escapeHtml(specs[s].id) + '</a></td>';
        html += '<td>' + escapeHtml(specs[s].created_at) + '</td></tr>';
      }
      html += '</tbody></table>';
    } else {
      html += '<p class="count">No specs yet.</p>';
    }

    if (task.status === 'blocked' && qaFailure) {
      html += '<h2>QA Failure</h2><pre class="warn">' + escapeHtml(qaFailure) + '</pre>';
    }

    html += '<h2>Execution History</h2>';
    if (executions.length > 0) {
      html += '<table><thead><tr><th>Execution ID</th><th>Status</th><th>Started</th><th>Completed</th><th>Failure Reason</th></tr></thead><tbody>';
      for (var e = 0; e < executions.length; e++) {
        var ex = executions[e];
        html += '<tr>';
        html += '<td><a href="/executions/' + escapeHtml(ex.id) + '">' + escapeHtml(ex.id) + '</a></td>';
        html += '<td><span class="badge badge-' + escapeHtml(ex.status) + '">' + statusLabel(ex.status) + '</span></td>';
        html += '<td>' + escapeHtml(ex.started_at) + '</td>';
        html += '<td>' + escapeHtml(ex.completed_at || '\u2014') + '</td>';
        html += '<td>' + (ex.failure_reason ? '<span class="warn">' + escapeHtml(ex.failure_reason) + '</span>' : '\u2014') + '</td>';
        html += '</tr>';
      }
      html += '</tbody></table>';
    } else {
      html += '<p class="count">No executions yet.</p>';
    }

    modalBody.innerHTML = html;
  }

  function openModal(taskId) {
    fetch('/api/tasks/' + taskId)
      .then(function (res) {
        if (!res.ok) throw new Error('Task not found');
        return res.json();
      })
      .then(function (data) {
        renderModal(data);
        backdrop.removeAttribute('hidden');
        subscribeSSE(taskId);
      })
      .catch(function (err) {
        modalBody.innerHTML = '<p class="warn">Failed to load task: ' + escapeHtml(err.message) + '</p>';
        backdrop.removeAttribute('hidden');
      });
  }

  function subscribeSSE(taskId) {
    closeSSE();
    activeEventSource = new EventSource('/api/tasks/' + taskId + '/events');
    activeEventSource.onmessage = function (evt) {
      try {
        var payload = JSON.parse(evt.data);
        var badge = document.getElementById('modal-status-badge');
        if (badge && payload.status) {
          badge.className = statusClass(payload.status);
          badge.textContent = statusLabel(payload.status);
        }
      } catch (e) {
        // ignore parse errors
      }
    };
  }

  function closeSSE() {
    if (activeEventSource) {
      activeEventSource.close();
      activeEventSource = null;
    }
  }

  function closeModal() {
    backdrop.setAttribute('hidden', '');
    closeSSE();
    modalBody.innerHTML = '';
  }

  // Delegated click listener for task rows
  document.addEventListener('click', function (evt) {
    var target = evt.target;
    // Walk up to find .task-row
    while (target && target !== document) {
      if (target.classList && target.classList.contains('task-row')) {
        // Don't open modal if clicking a link
        if (evt.target.tagName === 'A') return;
        var taskId = target.getAttribute('data-task-id');
        if (taskId) {
          evt.preventDefault();
          openModal(taskId);
        }
        return;
      }
      target = target.parentElement;
    }
  });

  // Close button
  closeBtn.addEventListener('click', closeModal);

  // Backdrop click (not modal itself)
  backdrop.addEventListener('click', function (evt) {
    if (evt.target === backdrop) {
      closeModal();
    }
  });

  // Escape key
  document.addEventListener('keydown', function (evt) {
    if (evt.key === 'Escape' && !backdrop.hasAttribute('hidden')) {
      closeModal();
    }
  });
})();
