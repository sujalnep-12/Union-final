/**
 * community_detail_interactive.js
 * Interactive behaviors for the community detail page.
 */

function qs(selector, ctx = document) {
  return ctx.querySelector(selector);
}

function qsa(selector, ctx = document) {
  return Array.from(ctx.querySelectorAll(selector));
}

function escHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

function initCopyCommunityUrl() {
  const button = qs('[data-copy-community-url]');
  if (!button || !navigator.clipboard) return;

  button.addEventListener('click', () => {
    navigator.clipboard.writeText(window.location.href)
      .then(() => {
        const icon = button.querySelector('i');
        if (icon) icon.className = 'fa-solid fa-check text-base';
        button.classList.add('bg-purple-600');
        showToast('Community link copied!');
        setTimeout(() => {
          if (icon) icon.className = 'fa-solid fa-share-nodes text-base';
          button.classList.remove('bg-purple-600');
        }, 1600);
      })
      .catch(() => showToast('Could not copy link', 'error'));
  });
}

function initLeaveCommunity() {
  qsa('[data-leave-community]').forEach(button => {
    button.addEventListener('click', () => {
      showToast('Leave community clicked', 'success');
    });
  });
}

function initTabs() {
  qsa('.bg-gray-100 .py-5').forEach(tab => {
    tab.addEventListener('click', event => {
      event.preventDefault();
      qsa('.bg-gray-100 .py-5').forEach(t => t.classList.remove('tab-active'));
      tab.classList.add('tab-active');
      showToast(`${tab.textContent.trim()} selected`, 'success');
    });
  });
}

function initNewPostButton() {
  qsa('[data-new-post-button]').forEach(button => {
    button.addEventListener('click', () => {
      showToast('Create new post flow coming soon', 'success');
    });
  });
}

function initLikeButtons() {
  qsa('button[data-like-button]').forEach(button => {
    button.addEventListener('click', () => {
      const icon = qs('i', button);
      const countEl = qs('[data-like-count]', button);
      if (!icon || !countEl) return;

      const count = Number(countEl.textContent.trim() || '0');
      const liked = icon.classList.contains('fa-solid');

      if (liked) {
        icon.classList.remove('fa-solid', 'text-red-500');
        icon.classList.add('fa-regular');
        countEl.textContent = String(Math.max(0, count - 1));
      } else {
        icon.classList.remove('fa-regular');
        icon.classList.add('fa-solid', 'text-red-500');
        countEl.textContent = String(count + 1);
      }
    });
  });
}

function toggleReplyPanel(panelId) {
  const panel = document.getElementById(panelId);
  if (!panel) return;
  panel.classList.toggle('hidden');
  if (!panel.classList.contains('hidden')) {
    const textarea = qs('textarea', panel);
    if (textarea) textarea.focus();
  }
}

function initCommentToggles() {
  qsa('[data-comment-toggle]').forEach(button => {
    button.addEventListener('click', () => {
      const panelId = button.getAttribute('data-comment-toggle');
      if (panelId) {
        toggleReplyPanel(panelId);
      }
    });
  });
}

function initAttachButtons() {
  qsa('[data-attach-button]').forEach(button => {
    button.addEventListener('click', () => {
      const panel = button.closest('[data-reply-panel]');
      const input = panel ? qs('[data-attach-input]', panel) : qs('[data-attach-input]');
      if (input) input.click();
    });
  });
}

function createAttachmentPreview(file) {
  const type = file.type || '';
  if (type.startsWith('image/')) {
    const url = URL.createObjectURL(file);
    return `<div class="rounded-3xl overflow-hidden bg-slate-50 border border-slate-200"><img src="${url}" class="w-full object-cover" alt="Attachment preview"></div>`;
  }

  if (type.startsWith('video/')) {
    const url = URL.createObjectURL(file);
    return `<div class="rounded-3xl overflow-hidden bg-slate-50 border border-slate-200"><video controls class="w-full"><source src="${url}" type="${type}">Your browser cannot play this video.</video></div>`;
  }

  return `<div class="rounded-3xl overflow-hidden bg-slate-50 border border-slate-200 px-4 py-3 text-sm text-slate-700">${escHtml(file.name)}</div>`;
}

function initAttachInputs() {
  qsa('input[data-attach-input]').forEach(input => {
    input.addEventListener('change', () => {
      const file = input.files && input.files[0];
      const panel = input.closest('[data-reply-panel]');
      const previewArea = panel ? qs('[data-attach-preview]', panel) : null;
      if (!file || !previewArea) return;

      previewArea.innerHTML = `
        <div class="flex items-start justify-between gap-4">
          <div class="flex-1">${createAttachmentPreview(file)}</div>
          <button type="button" class="rounded-full bg-slate-100 p-2 text-slate-500 hover:bg-slate-200" data-remove-attachment="true" aria-label="Remove attachment">
            <i class="fa-solid fa-xmark"></i>
          </button>
        </div>
      `;
      previewArea.classList.remove('hidden');

      qs('[data-remove-attachment]', previewArea)?.addEventListener('click', () => {
        if (previewArea) {
          previewArea.classList.add('hidden');
          previewArea.innerHTML = '';
        }
        input.value = '';
      });
    });
  });
}

function initEmojiButtons() {
  const emojiList = ['😊', '🎉', '🔥', '❤️', '🙌', '📸', '🎬'];

  qsa('[data-emoji-button]').forEach(button => {
    button.addEventListener('click', event => {
      const panel = button.closest('[data-reply-panel]');
      const textarea = panel ? qs('textarea', panel) : null;
      if (!textarea) {
        showToast('Open a reply box first to insert emoji', 'error');
        return;
      }

      const picker = document.createElement('div');
      picker.setAttribute('role', 'emoji-picker');
      picker.className = 'absolute z-50 mt-2 flex flex-wrap gap-2 rounded-3xl border border-slate-200 bg-white p-3 shadow-xl';
      picker.style.minWidth = '180px';
      picker.style.maxWidth = '220px';
      picker.style.right = '0';

      emojiList.forEach(emoji => {
        const item = document.createElement('button');
        item.type = 'button';
        item.className = 'h-9 w-9 rounded-2xl text-lg transition hover:bg-slate-100';
        item.textContent = emoji;
        item.addEventListener('click', () => {
          const start = textarea.selectionStart;
          const end = textarea.selectionEnd;
          textarea.value = textarea.value.slice(0, start) + emoji + textarea.value.slice(end);
          textarea.focus();
          textarea.selectionStart = textarea.selectionEnd = start + emoji.length;
          picker.remove();
        });
        picker.appendChild(item);
      });

      closeAllEmojiPickers();
      const wrapper = button.parentElement;
      if (wrapper) {
        wrapper.style.position = 'relative';
        wrapper.appendChild(picker);
      }
      event.stopPropagation();
    });
  });

  document.addEventListener('click', closeAllEmojiPickers);
}

function closeAllEmojiPickers() {
  qsa('div[role="emoji-picker"]').forEach(el => el.remove());
}

function initVoiceButtons() {
  qsa('[data-voice-button]').forEach(button => {
    let recorder = null;
    let chunks = [];
    let recording = false;

    button.addEventListener('click', async () => {
      const panel = button.closest('[data-reply-panel]');
      if (!panel) return;

      if (!recording) {
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
          showToast('Voice recording not supported', 'error');
          return;
        }

        try {
          const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
          recorder = new MediaRecorder(stream);
          chunks = [];
          recorder.ondataavailable = event => chunks.push(event.data);
          recorder.onstop = () => {
            const blob = new Blob(chunks, { type: 'audio/webm' });
            const url = URL.createObjectURL(blob);
            showAudioPreview(panel, url);
            stream.getTracks().forEach(track => track.stop());
          };
          recorder.start();
          recording = true;
          button.classList.add('text-rose-600');
          button.querySelector('i')?.classList.replace('fa-microphone', 'fa-stop');
          showToast('Recording started', 'success');
        } catch (error) {
          showToast('Microphone permission denied', 'error');
        }
      } else {
        recorder?.stop();
        recording = false;
        button.classList.remove('text-rose-600');
        button.querySelector('i')?.classList.replace('fa-stop', 'fa-microphone');
        showToast('Recording saved', 'success');
      }
    });
  });
}

function showAudioPreview(container, url) {
  const preview = document.createElement('div');
  preview.className = 'mt-3 flex items-center gap-3 rounded-2xl bg-purple-50 border border-purple-200 px-4 py-3';
  preview.innerHTML = `
    <audio controls src="${url}" class="h-9 flex-1 min-w-0"></audio>
    <button type="button" class="flex h-8 w-8 items-center justify-center rounded-full bg-slate-200 hover:bg-red-100 text-slate-600 hover:text-red-600" aria-label="Remove audio">
      <i class="fa-solid fa-xmark text-sm"></i>
    </button>
  `;

  preview.querySelector('button')?.addEventListener('click', () => preview.remove());
  container.appendChild(preview);
}

function initSendReplyButtons() {
  qsa('[data-send-reply]').forEach(button => {
    button.addEventListener('click', () => {
      const panel = button.closest('[data-reply-panel]');
      if (!panel) return;
      const textarea = qs('textarea', panel);
      const body = textarea?.value.trim();
      if (!body) {
        if (textarea) textarea.focus();
        showToast('Write something before sending', 'error');
        return;
      }
      const postCard = button.closest('.post-card');
      const count = postCard ? qs('[data-comment-count]', postCard) : null;
      if (count) {
        count.textContent = String(Number(count.textContent.trim() || '0') + 1);
      }
      textarea.value = '';
      const preview = qs('[data-attach-preview]', panel);
      if (preview) {
        preview.classList.add('hidden');
        preview.innerHTML = '';
      }
      const input = qs('input[data-attach-input]', panel);
      if (input) input.value = '';
      showToast('Reply posted', 'success');
    });
  });
}

function showToast(message, type = 'success') {
  const toast = document.createElement('div');
  toast.style.cssText = 'position:fixed;top:68px;right:16px;z-index:9999;background:#f0fdf4;color:#15803d;padding:6px 10px;border:1px solid #bbf7d0;border-radius:8px;font-size:11px;font-weight:600;line-height:1.35;box-shadow:0 4px 12px rgba(15,23,42,0.10);opacity:0;transition:opacity 180ms ease;';
  toast.innerHTML = `<i class="fa-solid ${type === 'success' ? 'fa-check' : 'fa-xmark'} mr-2"></i>${escHtml(message)}`;
  document.body.appendChild(toast);
  requestAnimationFrame(() => { toast.style.opacity = '1'; });
  setTimeout(() => {
    toast.style.opacity = '0';
    setTimeout(() => toast.remove(), 400);
  }, 2400);
}

function initCommunityDetailInteractive() {
  initCopyCommunityUrl();
  initLeaveCommunity();
  initTabs();
  initNewPostButton();
  initLikeButtons();
  initCommentToggles();
  initAttachButtons();
  initAttachInputs();
  initEmojiButtons();
  initVoiceButtons();
  initSendReplyButtons();
}

document.addEventListener('DOMContentLoaded', initCommunityDetailInteractive);
