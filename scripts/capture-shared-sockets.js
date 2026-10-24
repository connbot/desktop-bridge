// Test-only capture. Preserve native instances: Debian noVNC 1.3 checks channel
// methods on the immediate prototype, so subclassing WebSocket breaks attach().
(() => {
    window.WebSocket = new Proxy(window.WebSocket, {
        construct(target, args) {
            const socket = new target(...args);
            if (new URL(args[0]).pathname === '/desktop/shared') {
                (window.__sharedSockets ||= []).push(socket);
            }
            return socket;
        }
    });
})();
