import { Api } from '../core/api.js';
import { App } from './app.js';
import { AppState } from './state.js';

const root = document.getElementById('app');
if (root) {
  const state = new AppState(new Api());
  void new App(root, state).boot();
}
