/* Envio do arquivo de vídeo em blocos, com retomada. */
(function () {
  'use strict';

  var meta = document.querySelector('meta[name="csrf-token"]');
  var CSRF = meta ? meta.getAttribute('content') : '';

  var button = document.getElementById('start-upload');
  var fileInput = document.getElementById('file-input');
  var videoSelect = document.getElementById('video-select');
  var progressWrap = document.getElementById('upload-progress-wrap');
  var progressBar = document.getElementById('upload-progress');
  var message = document.getElementById('upload-message');

  if (!button) { return; }

  function setMessage(text, kind) {
    message.textContent = text;
    message.className = 'mt-2 text-xs ' + (kind === 'error' ? 'text-rose-600' : 'text-slate-500');
  }

  function setProgress(percent) {
    progressWrap.classList.remove('hidden');
    progressBar.style.width = percent.toFixed(1) + '%';
    progressBar.textContent = percent.toFixed(1) + '%';
  }

  function headers(extra) {
    var config = window.CREMEPE_UPLOAD || {};
    var result = { 'X-Requested-With': 'XMLHttpRequest' };
    if (config.apiToken) {
      result['Authorization'] = 'Bearer ' + config.apiToken;
      result['X-API-Token'] = config.apiToken;
    }
    if (extra) { Object.keys(extra).forEach(function (k) { result[k] = extra[k]; }); }
    return result;
  }

  function comToken(url) {
    var config = window.CREMEPE_UPLOAD || {};
    if (!config.apiToken) { return url; }
    return url + (url.indexOf('?') === -1 ? '?' : '&') +
      'api_token=' + encodeURIComponent(config.apiToken);
  }

  async function sha256(buffer) {
    if (!window.crypto || !window.crypto.subtle) { return null; }
    var digest = await window.crypto.subtle.digest('SHA-256', buffer);
    return Array.prototype.map.call(new Uint8Array(digest), function (b) {
      return b.toString(16).padStart(2, '0');
    }).join('');
  }

  async function upload() {
    var config = window.CREMEPE_UPLOAD || {};
    var file = fileInput.files && fileInput.files[0];
    var videoId = videoSelect.value;

    if (!config.initUrl || !config.chunkTemplate || !config.completeTemplate) {
      setMessage('Configuração de envio indisponível. Recarregue a página.', 'error');
      return;
    }
    if (!file) { setMessage('Escolha um arquivo para enviar.', 'error'); return; }
    if (!videoId) { setMessage('Crie e selecione um vídeo antes de enviar.', 'error'); return; }

    button.disabled = true;
    setProgress(0);
    setMessage('Preparando o envio...');

    try {
      var initResponse = await fetch(comToken(config.initUrl), {
        method: 'POST',
        headers: headers({ 'Content-Type': 'application/json', 'X-CSRFToken': CSRF }),
        credentials: 'same-origin',
        body: JSON.stringify({ video_id: parseInt(videoId, 10), filename: file.name, size: file.size })
      });
      if (!initResponse.ok) {
        var initError = await initResponse.json().catch(function () { return {}; });
        throw new Error(initError.error || 'Não foi possível iniciar o envio.');
      }
      var session = await initResponse.json();

      var chunkSize = session.chunk_size;
      var total = session.total_chunks;
      var received = session.received || [];
      var uploadedBytes = received.length * chunkSize;

      for (var index = 0; index < total; index++) {
        if (received.indexOf(index) !== -1) { continue; }

        var start = index * chunkSize;
        var end = Math.min(file.size, start + chunkSize);
        var blob = file.slice(start, end);
        var buffer = await blob.arrayBuffer();
        var checksum = await sha256(buffer);

        var extra = {
          'Content-Type': 'application/octet-stream',
          'X-Chunk-Index': index,
          'X-CSRFToken': CSRF
        };
        if (checksum) { extra['X-Chunk-SHA256'] = checksum; }

        var response = await fetch(comToken(config.chunkTemplate.replace(config.placeholder, session.upload_id)), {
          method: 'POST',
          headers: headers(extra),
          credentials: 'same-origin',
          body: buffer
        });
        if (!response.ok) {
          var payload = await response.json().catch(function () { return {}; });
          throw new Error(payload.error || ('Falha ao enviar o bloco ' + (index + 1) + '.'));
        }

        uploadedBytes += (end - start);
        setProgress((uploadedBytes / file.size) * 100);
        setMessage('Enviando... ' + Math.round((uploadedBytes / file.size) * 100) + '%');
      }

      setMessage('Montando o arquivo final...');
      var complete = await fetch(comToken(config.completeTemplate.replace(config.placeholder, session.upload_id)), {
        method: 'POST',
        headers: headers({ 'X-CSRFToken': CSRF }),
        credentials: 'same-origin'
      });
      if (!complete.ok) {
        var errorPayload = await complete.json().catch(function () { return {}; });
        throw new Error(errorPayload.error || 'Falha ao concluir o envio.');
      }

      setProgress(100);
      setMessage('Arquivo enviado! O vídeo entrou na fila de processamento.');
      button.disabled = false;
    } catch (error) {
      setMessage(error.message || 'Erro durante o envio.', 'error');
      button.disabled = false;
    }
  }

  button.addEventListener('click', upload);
})();
