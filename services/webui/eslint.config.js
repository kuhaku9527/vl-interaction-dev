// eslint.config.js — flat config for JoyAI webui static assets.
//
// SCOPE (deliberately an explicit allowlist — never a glob).
//
// These 9 modules are the ones covered by this gate at the last CI-green
// commit (`b637dfa`, 2026-08-11, Quality Gate run 31479401047 → job `eslint`
// = success). They are the original "Phase 0" IIFE-to-window modules plus the
// two that were added while the gate stayed green and CI validated them.
//
// Do NOT widen this back to `static/**/*.js` without doing the follow-up work.
// History: the gate originally used the glob `static/**/*.js` when only 7
// files matched. The v6-lite split (2026-08-13..17, starting at bc7ca55)
// moved ~1934 lines of inline JS out of index.html into 16 NEW modules in the
// same directory. Those modules reference variables still declared by
// index.html's inline script through shared global scope (by script load
// order), and the `globals` list below was never extended for them — so the
// glob silently swept in 16 files that were never in scope, turning the gate
// red with 1297 errors (1056 `no-undef`). Because quality.yml only triggers on
// push:[main] / PR-to-main and the UI branch was never PR'd, CI never ran on
// that branch and this went unnoticed for ~5 weeks.
//
// ⇒ The 16 split-out modules are OUT OF SCOPE here and tracked as separate
//    follow-up work. An explicit list means a newly added module must be
//    opted in deliberately instead of silently inheriting this gate.
//
// The ~5600 lines of inline JS inside index.html remain out of scope (see
// lint-review-and-expansion report, Phase 3).
import js from '@eslint/js';

// In-scope modules, relative to this config file (services/webui).
const IN_SCOPE = [
  'src/joy_interaction_webui/static/capture_rtsp.js',
  'src/joy_interaction_webui/static/capture_webcam.js',
  'src/joy_interaction_webui/static/config_services.js',
  'src/joy_interaction_webui/static/i18n_device_label.js',
  'src/joy_interaction_webui/static/joy_ws.js',
  'src/joy_interaction_webui/static/render_markdown.js',
  'src/joy_interaction_webui/static/sanitize_static_html.js',
  'src/joy_interaction_webui/static/screen_capture.js',
  'src/joy_interaction_webui/static/wiki_frontend.js',
];

export default [
  {
    files: IN_SCOPE,
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'script',
      globals: {
        window: 'writable',
        document: 'readonly',
        console: 'readonly',
        performance: 'readonly',
        navigator: 'readonly',
        WebSocket: 'readonly',
        RTCPeerConnection: 'readonly',
        RTCSessionDescription: 'readonly',
        MediaStream: 'readonly',
        MediaRecorder: 'readonly',
        ImageCapture: 'readonly',
        Node: 'readonly',
        DOMParser: 'readonly',
        HTMLElement: 'readonly',
        HTMLVideoElement: 'readonly',
        HTMLCanvasElement: 'readonly',
        AudioContext: 'readonly',
        URL: 'readonly',
        Blob: 'readonly',
        marked: 'readonly',
        DOMPurify: 'readonly',
        katex: 'readonly',
        fetch: 'readonly',
        location: 'readonly',
        alert: 'readonly',
        confirm: 'readonly',
        setTimeout: 'readonly',
        setInterval: 'readonly',
        clearInterval: 'readonly',
        clearTimeout: 'readonly',
        requestAnimationFrame: 'readonly',
        cancelAnimationFrame: 'readonly',
        // Genuine gaps in this list (they are ordinary browser globals and the
        // in-scope modules have always used them):
        //   localStorage — config_services.js provider-preset persistence
        //   crypto       — config_services.js random id generation
        localStorage: 'readonly',
        crypto: 'readonly',
      },
    },
    rules: {
      // Baseline "real bug" rules.
      'no-undef': 'error',
      'no-unused-vars': ['error', { argsIgnorePattern: '^_', varsIgnorePattern: '^_', caughtErrorsIgnorePattern: '^_' }],
      // Style rules that the in-scope files already follow.
      'strict': ['error', 'global'], // require 'use strict'
      'quotes': ['error', 'single', { avoidEscape: true }],
      'semi': ['error', 'always'],
      // UI files legitimately log to the console.
      'no-console': 'off',
      'no-empty': ['error', { allowEmptyCatch: true }],
    },
  },
  {
    ignores: [
      '**/.venv/**',
      'node_modules/**',
      'dist/**',
      '**/__pycache__/**',
      'eslint.config.js',
    ],
  },
];
