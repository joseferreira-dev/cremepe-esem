/* Player do curso: HLS + bloqueio de avanço + heartbeat de progresso. */
(function () {
  'use strict';

  var CONFIG = window.CREMEPE || {};
  var meta = document.querySelector('meta[name="csrf-token"]');
  var CSRF = meta ? meta.getAttribute('content') : '';
  // Token de API: permite ao player funcionar mesmo que o navegador/sistema
  // reescreva o cabecalho "Cookie" das requisicoes fetch.
  var metaApi = document.querySelector('meta[name="api-token"]');
  var API_TOKEN = metaApi ? metaApi.getAttribute('content') : '';

  var player = document.getElementById('player');
  var titleEl = document.getElementById('video-title');
  var infoEl = document.getElementById('player-info');
  var warningEl = document.getElementById('player-warning');
  var fillEl = document.getElementById('watched-fill');
  var listEl = document.getElementById('video-list');

  if (!player || !listEl) {
    console.error(
      'CREMEPE: player sem elementos do DOM (' +
      (player ? '' : 'player, ') + (listEl ? '' : 'video-list') + ').'
    );
    return;
  }
  if (!CONFIG.stateUrl) {
    console.error('CREMEPE: configuração do player ausente (window.CREMEPE).');
    return;
  }

  var enrollmentId = CONFIG.enrollmentId;
  var currentVideoId = null;
  var furthest = 0;
  var maxAllowed = 0;
  var duration = 0;
  var hls = null;
  var heartbeatTimer = null;
  var warningTimer = null;
  var recovering = false;
  var sessionExpired = false;
  var MAX_PLAYBACK_RATE = 1.25;

  // Ao retomar, nunca pular para alem do trecho liberado. Sem isso, um
  // last_position antigo (maior que furthest) faz o hls.js pedir um segmento
  // proibido e o video entra em loop de erro 403.
  function resumePosition(value) {
    var posicao = Math.max(0, value || 0);
    if (maxAllowed > 0 && posicao > maxAllowed) {
      posicao = maxAllowed;
    }
    return posicao;
  }

  function formatTime(seconds) {
    seconds = Math.max(0, Math.floor(seconds || 0));
    var h = Math.floor(seconds / 3600);
    var m = Math.floor((seconds % 3600) / 60);
    var s = seconds % 60;
    if (h > 0) { return h + 'h' + String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0'); }
    return m + ':' + String(s).padStart(2, '0');
  }

  function showWarning(message) {
    if (message) { warningEl.textContent = message; }
    warningEl.classList.remove('hidden');
    if (warningTimer) { clearTimeout(warningTimer); }
    warningTimer = setTimeout(function () { warningEl.classList.add('hidden'); }, 6000);
  }

  function updateBar() {
    var percent = duration > 0 ? Math.min(100, (furthest / duration) * 100) : 0;
    fillEl.style.width = percent.toFixed(2) + '%';
    infoEl.textContent = 'Assistido: ' + formatTime(furthest) + ' / ' + formatTime(duration);
  }

  // Uma aba antiga pode disparar o player com um cookie de sessao orfao.
  // O servidor nao apaga mais o cookie nesse caso; aqui recarregamos uma vez
  // para aproveitar a sessao valida que o navegador tem para o site.
  function handleSessionExpired() {
    if (sessionExpired) { return; }
    sessionExpired = true;
    if (heartbeatTimer) {
      clearInterval(heartbeatTimer);
      heartbeatTimer = null;
    }

    var chave = 'cremepe_reload_401';
    var agora = Date.now();
    var ultimo = 0;
    try { ultimo = parseInt(sessionStorage.getItem(chave) || '0', 10); } catch (e) { /* ignore */ }

    if (!ultimo || agora - ultimo > 15000) {
      try { sessionStorage.setItem(chave, String(agora)); } catch (e) { /* ignore */ }
      showWarning('Sua sess\u00e3o expirou. Recarregando a p\u00e1gina...');
      setTimeout(function () { window.location.reload(); }, 900);
      return;
    }

    showWarning('Sua sess\u00e3o expirou. Entre novamente no portal.');
    var aviso = warningEl.querySelector('a');
    if (!aviso) {
      aviso = document.createElement('a');
      aviso.href = '/entrar/?next=' + encodeURIComponent(window.location.pathname);
      aviso.className = 'ml-2 font-semibold underline';
      aviso.textContent = 'Entrar novamente';
      warningEl.appendChild(aviso);
    }
  }

  function headers(extra) {
    var result = { 'X-Requested-With': 'XMLHttpRequest' };
    if (API_TOKEN) {
      result['Authorization'] = 'Bearer ' + API_TOKEN;
      result['X-API-Token'] = API_TOKEN;
    }
    if (extra) { Object.keys(extra).forEach(function (k) { result[k] = extra[k]; }); }
    return result;
  }

  function comToken(url) {
    if (!API_TOKEN) { return url; }
    return url + (url.indexOf('?') === -1 ? '?' : '&') +
      'api_token=' + encodeURIComponent(API_TOKEN);
  }

  // A pagina pode ter sido aberta antes do login ou o navegador pode ter
  // cookies de sessao com paths diferentes. Esta rota fica em /cursos/ e
  // devolve um token novo usando a sessao que autenticou a propria pagina.
  var tokenPromise = null;
  function obterToken() {
    if (!CONFIG.tokenUrl) { return Promise.resolve(API_TOKEN); }
    if (tokenPromise) { return tokenPromise; }
    tokenPromise = fetch(CONFIG.tokenUrl, {
      headers: { 'X-Requested-With': 'XMLHttpRequest' },
      credentials: 'same-origin',
      cache: 'no-store'
    })
      .then(function (response) {
        if (response.status === 401) {
          handleSessionExpired();
          return API_TOKEN;
        }
        if (!response.ok) { return API_TOKEN; }
        return response.json().then(function (data) {
          if (data && data.api_token) { API_TOKEN = data.api_token; }
          return API_TOKEN;
        });
      })
      .catch(function () { return API_TOKEN; });
    return tokenPromise;
  }

  function loadVideo(videoId) {
    if (!videoId) { return; }
    currentVideoId = videoId;

    obterToken()
      .then(function () {
        return fetch(comToken(CONFIG.stateUrl + '?enrollment=' + enrollmentId + '&video=' + videoId), {
          headers: headers(),
          credentials: 'same-origin'
        });
      })
      .then(function (response) {
        if (response.status === 401) {
          handleSessionExpired();
          return null;
        }
        if (!response.ok) { throw new Error('Falha ao carregar o estado do v�deo.'); }
        return response.json();
      })
      .then(function (state) {
        if (!state) { return; }
        if (!state.unlocked) {
          showWarning('Este vídeo ainda não foi liberado. Assista ao anterior até o fim.');
          return;
        }
        duration = state.duration || 0;
        furthest = state.furthest || 0;
        maxAllowed = state.max_allowed || 0;
        CONFIG.heartbeatInterval = state.heartbeat_interval || CONFIG.heartbeatInterval || 10;
        titleEl.textContent = state.video_title;
        updateBar();

        if (hls) { hls.destroy(); hls = null; }
        player.removeAttribute('src');
        player.load();

        if (window.Hls && window.Hls.isSupported()) {
          hls = new window.Hls({
            enableWorker: true,
            backBufferLength: 30,
            // O servidor libera segmentos ate ~18 s a frente do ultimo
            // heartbeat. Como o furthest pode ficar ate 10 s atras do player,
            // o buffer seguro e ~8 s. O padrao do hls.js (30 s) pedia muito
            // alem disso e gerava uma enxurrada de 403; 6 s (2 segmentos)
            // cabe na margem sem interromper a reproducao.
            maxBufferLength: 6,
            maxMaxBufferLength: 10,
            // Token de API: o hls.js tambem precisa se autenticar.
            xhrSetup: function (xhr) {
              if (API_TOKEN) {
                xhr.setRequestHeader('Authorization', 'Bearer ' + API_TOKEN);
                xhr.setRequestHeader('X-API-Token', API_TOKEN);
              }
            }
          });
          hls.loadSource(comToken(state.manifest_url));
          hls.attachMedia(player);
          hls.on(window.Hls.Events.MANIFEST_PARSED, function () {
            var retomar = resumePosition(state.last_position);
            if (retomar > 0) { player.currentTime = retomar; }
          });
          hls.on(window.Hls.Events.ERROR, function (event, data) {
            if (!data || !data.fatal) { return; }
            if (data.type === window.Hls.ErrorTypes.NETWORK_ERROR) {
              // Pode ser segmento alem do limite ou token expirado. O
              // heartbeat devolve a posicao autorizada e corrige o player.
              if (recovering) { return; }
              recovering = true;
              sendHeartbeat().then(function () {
                recovering = false;
                if (hls) { hls.startLoad(); }
              });
            } else if (data.type === window.Hls.ErrorTypes.MEDIA_ERROR) {
              if (hls) { hls.recoverMediaError(); }
            } else {
              showWarning('Não foi possível reproduzir este trecho. Recarregue a página.');
            }
          });
        } else if (player.canPlayType('application/vnd.apple.mpegurl')) {
          player.src = comToken(state.manifest_url);
          player.addEventListener('loadedmetadata', function () {
            var retomar = resumePosition(state.last_position);
            if (retomar > 0) { player.currentTime = retomar; }
          }, { once: true });
        } else {
          showWarning('Seu navegador não suporta a reprodução deste formato.');
        }

        startHeartbeat();
        markActive(videoId);
      })
      .catch(function (error) {
        var mensagem = (error && error.message) ? error.message : 'erro desconhecido';
        showWarning(
          'Nǜo foi poss��vel carregar o v��deo. Atualize a pǭgina com Ctrl+F5 (F5) ' +
          'e tente novamente. Detalhe: ' + mensagem
        );
      });
  }

  function markActive(videoId) {
    Array.prototype.forEach.call(listEl.querySelectorAll('.video-item'), function (item) {
      item.classList.toggle('active', item.getAttribute('data-video-id') === String(videoId));
    });
  }

  function sendHeartbeat() {
    if (!currentVideoId) { return Promise.resolve(null); }
    var playing = !player.paused && !player.ended && player.readyState > 0;
    var body = JSON.stringify({
      enrollment: enrollmentId,
      video: currentVideoId,
      position: player.currentTime || 0,
      playing: playing
    });

    return fetch(comToken(CONFIG.heartbeatUrl), {
      method: 'POST',
      headers: headers({ 'Content-Type': 'application/json', 'X-CSRFToken': CSRF }),
      body: body,
      credentials: 'same-origin',
      keepalive: true
    })
      .then(function (response) {
        if (response.status === 401) {
          handleSessionExpired();
          return null;
        }
        if (!response.ok) { return null; }
        return response.json();
      })
      .then(function (data) {
        if (!data) { return null; }
        furthest = data.furthest;
        maxAllowed = data.max_allowed;
        updateBar();

        var autorizada = data.authorized_position || 0;
        var delta = Math.abs((player.currentTime || 0) - autorizada);
        if (delta > 1.5) {
          // Inclui o caso authorized_position = 0 (retomar do inicio), que
          // antes era ignorado e deixava o player preso em um segmento 403.
          player.currentTime = autorizada;
          showWarning('O avanço além do conteúdo já assistido não é permitido.');
        }
        return data;
      })
      .catch(function () { return null; });
  }

  function startHeartbeat() {
    if (heartbeatTimer) { clearInterval(heartbeatTimer); }
    var interval = (CONFIG.heartbeatInterval || 10) * 1000;
    heartbeatTimer = setInterval(sendHeartbeat, interval);
  }

  // Camada A: impede o arrastar da barra além do ponto liberado.
  player.addEventListener('seeking', function () {
    if (maxAllowed > 0 && player.currentTime > maxAllowed + 0.75) {
      player.currentTime = Math.max(0, maxAllowed);
      showWarning('Não é possível avançar para além do conteúdo que você já assistiu.');
      sendHeartbeat();
    }
  });

  player.addEventListener('ratechange', function () {
    if (player.playbackRate > MAX_PLAYBACK_RATE) {
      player.playbackRate = MAX_PLAYBACK_RATE;
      showWarning('A velocidade máxima permitida é ' + MAX_PLAYBACK_RATE + 'x.');
    }
  });

  player.addEventListener('pause', function () { sendHeartbeat(); });
  player.addEventListener('ended', function () { sendHeartbeat(); });
  player.addEventListener('play', function () { sendHeartbeat(); });

  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'hidden') { sendHeartbeat(); }
  });

  window.addEventListener('beforeunload', function () {
    if (!currentVideoId) { return; }
    try {
      fetch(comToken(CONFIG.heartbeatUrl), {
        method: 'POST',
        headers: headers({ 'Content-Type': 'application/json', 'X-CSRFToken': CSRF }),
        body: JSON.stringify({
          enrollment: enrollmentId,
          video: currentVideoId,
          position: player.currentTime || 0,
          playing: false
        }),
        credentials: 'same-origin',
        keepalive: true
      });
    } catch (e) { /* ignore */ }
  });

  listEl.addEventListener('click', function (event) {
    var item = event.target.closest('.video-item');
    if (!item) { return; }
    var videoId = item.getAttribute('data-video-id');
    if (item.getAttribute('data-unlocked') !== '1') {
      showWarning('Este vídeo ainda não foi liberado. Assista ao anterior até o fim.');
      return;
    }
    loadVideo(videoId);
  });

  // Carrega o primeiro vídeo liberado (ou o último assistido).
  function iniciar() {
    var initial = listEl.querySelector('.video-item[data-unlocked="1"]');
    if (initial) {
      loadVideo(initial.getAttribute('data-video-id'));
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', iniciar);
  } else {
    iniciar();
  }
})();
