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

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') {
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
})();
