/* Executa o player.js contra o HTML real servido, com um DOM simulado.
   Verifica se o player encontra a lista de vídeos e chama a API de estado.

   node scripts/simular_player.js <arquivo-html>
*/
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const htmlPath = process.argv[2];
const html = fs.readFileSync(htmlPath, 'utf-8');

function idExiste(id) {
  return new RegExp(`id="${id}"`).test(html);
}

function elemento(id) {
  return {
    id: id,
    textContent: '',
    innerHTML: '',
    style: {},
    classList: {
      _classes: new Set(),
      add(c) { this._classes.add(c); },
      remove(c) { this._classes.delete(c); },
      toggle(c, force) { force ? this._classes.add(c) : this._classes.delete(c); },
      contains(c) { return this._classes.has(c); },
    },
    addEventListener() {},
    removeAttribute() {},
    load() {},
    getAttribute(nome) { return this._attrs && this._attrs[nome] !== undefined ? this._attrs[nome] : null; },
    setAttribute(nome, valor) { this._attrs = this._attrs || {}; this._attrs[nome] = valor; },
    _attrs: {},
    querySelectorAll() { return []; },
    querySelector(seletor) {
      if (seletor === '.video-item[data-unlocked="1"]') {
        const achou = /class="[^"]*video-item[^"]*"[\s\S]{0,400}?data-video-id="(\d+)"[\s\S]{0,400}?data-unlocked="1"/.exec(html);
        if (achou) {
          return { getAttribute: (n) => (n === 'data-video-id' ? achou[1] : '1') };
        }
      }
      return null;
    },
  };
}

const elementos = {};
['player', 'video-list', 'video-title', 'player-info', 'player-warning', 'watched-fill']
  .forEach((id) => { elementos[id] = elemento(id); });

const requisicoes = [];

const contexto = {
  console: console,
  setTimeout: () => 0,
  clearTimeout: () => {},
  setInterval: () => 0,
  clearInterval: () => {},
  Promise: Promise,
  Object: Object,
  Array: Array,
  Math: Math,
  String: String,
  Number: Number,
  Error: Error,
  JSON: JSON,
  Date: Date,
  document: {
    readyState: 'complete',
    getElementById: (id) => (id in elementos ? elementos[id] : null),
    querySelector: (sel) => (sel.indexOf('csrf-token') !== -1
      ? { getAttribute: () => 'token-csrf-fake' }
      : null),
    querySelectorAll: () => [],
    addEventListener: () => {},
  },
  window: {
    CREMEPE: {
      enrollmentId: '1',
      heartbeatUrl: '/api/player/heartbeat/',
      stateUrl: '/api/player/state/',
    },
    Hls: undefined,
    addEventListener: () => {},
  },
  fetch: (url, opcoes) => {
    requisicoes.push(url);
    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve({
        video_id: 1,
        video_title: 'Encontro 1',
        duration: 14008,
        last_position: 0,
        furthest: 0,
        max_allowed: 18,
        covered: 0,
        unlocked: true,
        unlocked_videos: [1],
        heartbeat_interval: 10,
        manifest_url: '/api/media/1/master.m3u8?token=abc',
      }),
    });
  },
};
contexto.window.document = contexto.document;
contexto.globalThis = contexto;

const codigo = fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'player.js'), 'utf-8');
vm.createContext(contexto);
vm.runInContext(codigo, contexto, { filename: 'player.js' });

setTimeout(() => {
  console.log('IDs obrigatórios na página:');
  ['player', 'video-list', 'video-title', 'player-info', 'player-warning', 'watched-fill']
    .forEach((id) => console.log(`  id="${id}" -> ${idExiste(id) ? 'presente' : 'AUSENTE'}`));
  console.log('\nRequisições disparadas pelo player:');
  requisicoes.forEach((u) => console.log('  ' + u));
  if (requisicoes.length === 0) {
    console.log('\nFALHA: o player nao disparou nenhuma requisicao.');
    process.exit(1);
  }
  console.log('\nOK: o player carregou o estado do video.');
}, 50);
