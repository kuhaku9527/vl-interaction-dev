// window.JoyState — single source of truth for cross-module runtime state.
//
// Several values were previously loose top-level `let` bindings in index.html's
// inline script (or declared in one module) and shared across the classic
// <script> files only via the *implicit* global lexical environment. That works
// but is invisible and fragile: load-order dependent, easy to shadow, and a
// bare `x = y` in another file silently creates an implicit global.
//
// They are collected here so every module reads/writes the same explicit
// instance: window.JoyState. Loaded as the FIRST app script (before
// screen_capture.js) so it exists before any module's runtime code executes.
//
// NOTE: `sessionId` is intentionally NOT here — it was already an explicit
// `window.sessionId` (a window property, accessible as a bare global), so it
// was never the fragile lexical-shared case. We only removed its redundant
// local `let` mirror and fixed the bare reassignments in ws_dispatcher.js.
(function () {
    'use strict';

    function initialMarkdownEnabled() {
        // Default ON; persisted in localStorage. Guard in case storage is blocked.
        try {
            return localStorage.getItem('markdownEnabled') !== 'false';
        } catch (e) {
            return true;
        }
    }

    window.JoyState = {
        // Markdown rendering toggle (persisted by the toggle handler in vlm_render.js).
        markdownEnabled: initialMarkdownEnabled(),
        // Last user-typed prompt text, shared between the input UI (index.html) and
        // the history modules (vlm_history.js, ws_dispatcher.js).
        currentPromptText: '',
        // Monotonic generation counter for LLM replies — the stale-reply guard.
        // Raised by llm_reply_ui.js from backend payloads; originally declared in
        // llm_reply_audio.js, now centralized to make the cross-module dependency explicit.
        llmReplyGeneration: 0,
    };
})();
