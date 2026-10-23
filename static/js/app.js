/* Comportamentos leves da interface (sem framework). */
(function () {
  'use strict';

  function fecharMenus() {
    document.querySelectorAll('[data-dropdown-menu]').forEach(function (menu) {
      menu.classList.add('hidden');
    });
  }

  document.addEventListener('click', function (event) {
    var alternador = event.target.closest('[data-dropdown-toggle]');
    if (alternador) {
      var alvo = document.getElementById(alternador.getAttribute('data-dropdown-toggle'));
      if (alvo) {
        var estavaAberto = !alvo.classList.contains('hidden');
        fecharMenus();
        if (!estavaAberto) {
          alvo.classList.remove('hidden');
        }
      }
      event.preventDefault();
      return;
    }
    if (!event.target.closest('[data-dropdown-menu]')) {
      fecharMenus();
    }
  });

  var modal = document.querySelector('[data-modal]');
  var formularioPendente = null;

  function fecharModal() {
    if (!modal) {
      return;
    }
    modal.classList.add('hidden');
    modal.classList.remove('flex');
    formularioPendente = null;
  }

  function abrirModal(formulario) {
    if (!modal) {
      formulario.submit();
      return;
    }
    formularioPendente = formulario;
    var mensagem = modal.querySelector('[data-modal-message]');
    if (mensagem) {
      mensagem.textContent = formulario.getAttribute('data-confirm') || '';
    }
    modal.classList.remove('hidden');
    modal.classList.add('flex');
    var confirmar = modal.querySelector('[data-modal-confirm]');
    if (confirmar) {
      confirmar.focus();
    }
  }

  function atualizarLinhaMatricula(formulario, dados) {
    var linha = formulario.closest('[data-matricula-row]');
    if (!linha) {
      return;
    }
    var badge = linha.querySelector('[data-status-badge]');
    if (badge) {
      badge.textContent = dados.status_display || '';
      badge.className = 'etiqueta ' + (dados.status_class || '');
    }
    var aprovar = linha.querySelector('[data-acao="aprovar"]');
    if (aprovar) {
      aprovar.classList.toggle(
        'hidden',
        dados.status === 'approved' || dados.status === 'cancelled'
      );
    }
    var reprovar = linha.querySelector('[data-acao="reprovar"]');
    if (reprovar) {
      reprovar.classList.toggle(
        'hidden',
        dados.status === 'rejected' || dados.status === 'cancelled'
      );
    }
  }

  function enviarFormularioAjax(formulario) {
    var dados = new FormData(formulario);
    fetch(formulario.action, {
      method: 'POST',
      body: dados,
      credentials: 'same-origin',
      headers: { 'X-Requested-With': 'XMLHttpRequest' }
    })
      .then(function (resposta) {
        return resposta.json().then(function (corpo) {
          if (!resposta.ok || !corpo.ok) {
            throw new Error(corpo.mensagem || 'Não foi possível concluir a ação.');
          }
          atualizarLinhaMatricula(formulario, corpo);
        });
      })
      .catch(function () {
        window.location.reload();
      });
  }

  document.addEventListener('submit', function (event) {
    var formulario = event.target;
    if (!formulario.matches('form[data-confirm]')) {
      if (formulario.matches('form[data-ajax="1"]')) {
        event.preventDefault();
        enviarFormularioAjax(formulario);
      }
      return;
    }
    if (formulario.dataset.confirmado === '1') {
      return;
    }
    event.preventDefault();
    abrirModal(formulario);
  });

  if (modal) {
    modal.addEventListener('click', function (event) {
      if (event.target.closest('[data-modal-cancel]')) {
        fecharModal();
        return;
      }
      if (event.target.closest('[data-modal-confirm]')) {
        var formulario = formularioPendente;
        fecharModal();
        if (!formulario) {
          return;
        }
        if (formulario.dataset.ajax === '1') {
          enviarFormularioAjax(formulario);
        } else {
          formulario.dataset.confirmado = '1';
          formulario.submit();
        }
      }
    });
  }

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') {
      fecharModal();
      fecharMenus();
    }
  });

  var botao = document.querySelector('[data-menu-toggle]');
  var menu = document.querySelector('[data-menu]');
  if (botao && menu) {
    botao.addEventListener('click', function () {
      menu.classList.toggle('hidden');
    });
  }

  document.querySelectorAll('[data-tabs]').forEach(function (tabs) {
    var botoes = Array.prototype.slice.call(tabs.querySelectorAll('[data-tab-target]'));
    var paineis = Array.prototype.slice.call(tabs.querySelectorAll('[data-tab-panel]'));
    if (!botoes.length) {
      return;
    }

    function ativar(botao) {
      var alvo = botao.getAttribute('data-tab-target');
      botoes.forEach(function (item) {
        var ativo = item === botao;
        item.classList.toggle('border-marca-500', ativo);
        item.classList.toggle('text-marca-700', ativo);
        item.classList.toggle('border-transparent', !ativo);
        item.classList.toggle('text-slate-500', !ativo);
        item.setAttribute('aria-selected', ativo ? 'true' : 'false');
      });
      paineis.forEach(function (painel) {
        painel.classList.toggle(
          'hidden',
          painel.getAttribute('data-tab-panel') !== alvo
        );
      });
      if (window.history && window.history.replaceState) {
        window.history.replaceState(null, '', '#' + alvo);
      }
    }

    botoes.forEach(function (botao) {
      botao.addEventListener('click', function (event) {
        event.preventDefault();
        ativar(botao);
      });
    });

    tabs.querySelectorAll('[data-tab-link]').forEach(function (link) {
      link.addEventListener('click', function (event) {
        event.preventDefault();
        var alvo = link.getAttribute('data-tab-link');
        var botao = botoes.filter(function (item) {
          return item.getAttribute('data-tab-target') === alvo;
        })[0];
        if (botao) {
          ativar(botao);
        }
      });
    });

    var hash = (window.location.hash || '').replace('#', '');
    var inicial = botoes.filter(function (botao) {
      return botao.getAttribute('data-tab-target') === hash;
    })[0];
    ativar(inicial || botoes[0]);
  });

  // Filtro de lista (checkboxes multipla escolha).
  document.querySelectorAll('[data-filtro-entrada]').forEach(function (entrada) {
    var bloco = entrada.parentElement;
    var lista = bloco ? bloco.querySelector('[data-filtro-lista]') : null;
    if (!lista) { return; }
    var itens = lista.querySelectorAll('[data-filtro-item]');
    entrada.addEventListener('input', function () {
      var termo = entrada.value.trim().toLowerCase();
      itens.forEach(function (item) {
        var rotulo = item.getAttribute('data-rotulo') || '';
        item.classList.toggle('hidden', termo !== '' && rotulo.indexOf(termo) === -1);
      });
    });
  });

  // Campos de busca e seleção (substituem <select> com muitas opções).
  document.querySelectorAll('[data-busca]').forEach(function (bloco) {
    var select = bloco.querySelector('.campo-busca-select');
    var entrada = bloco.querySelector('[data-busca-entrada]');
    var lista = bloco.querySelector('[data-busca-lista]');
    if (!select || !entrada || !lista) { return; }

    var opcoes = Array.prototype.map.call(select.options, function (op) {
      return { valor: op.value, rotulo: (op.textContent || '').trim(), desativada: op.disabled };
    });

    function selecionada() {
      var atual = opcoes.filter(function (o) { return o.valor === select.value; })[0];
      return atual ? atual.rotulo : '';
    }

    function desenhar(termo) {
      var filtradas = opcoes.filter(function (o) {
        if (!o.valor) { return false; }
        if (!termo) { return true; }
        return o.rotulo.toLowerCase().indexOf(termo) !== -1;
      });
      lista.innerHTML = '';
      if (!filtradas.length) {
        var vazio = document.createElement('div');
        vazio.className = 'campo-busca-vazio';
        vazio.textContent = 'Nenhum resultado.';
        lista.appendChild(vazio);
        return;
      }
      filtradas.forEach(function (o) {
        var item = document.createElement('button');
        item.type = 'button';
        item.className = 'campo-busca-item';
        item.textContent = o.rotulo;
        item.setAttribute('aria-selected', o.valor === select.value ? 'true' : 'false');
        item.addEventListener('click', function () {
          select.value = o.valor;
          entrada.value = o.rotulo;
          lista.classList.add('hidden');
          select.dispatchEvent(new Event('change', { bubbles: true }));
        });
        lista.appendChild(item);
      });
    }

    function abrirLista() {
      desenhar(entrada.value.trim().toLowerCase() === selecionada().toLowerCase() ? '' : entrada.value.trim().toLowerCase());
      lista.classList.remove('hidden');
    }

    entrada.value = selecionada();
    entrada.addEventListener('focus', function () {
      entrada.select();
      abrirLista();
    });
    entrada.addEventListener('input', function () {
      desenhar(entrada.value.trim().toLowerCase());
      lista.classList.remove('hidden');
    });
    entrada.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') { lista.classList.add('hidden'); }
      if (event.key === 'Enter') {
        event.preventDefault();
        var primeiro = lista.querySelector('.campo-busca-item');
        if (primeiro) { primeiro.click(); }
      }
    });
    document.addEventListener('click', function (event) {
      if (!bloco.contains(event.target)) { lista.classList.add('hidden'); }
    });
  });
})();
