// Preload: безопасно прокидывает в окно только то, что нужно.
// Здесь минимум — порт сервера. Всё общение идёт через websocket из renderer.
const { contextBridge } = require("electron");

contextBridge.exposeInMainWorld("winbuddy", {
  serverPort: 8756,
});
