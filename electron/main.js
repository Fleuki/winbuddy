// Главный процесс Electron.
//
// Что делает:
//   1. Запускает Python-сервер (winbuddy.agent.server) как дочерний процесс.
//   2. Ждёт, пока сервер поднимется (пинг /health).
//   3. Открывает окно с интерфейсом (index.html).
//   4. При закрытии окна — гасит Python-процесс, чтобы не висел в фоне.
//
// Python берётся из venv проекта (.venv\Scripts\python.exe на Windows).

const { app, BrowserWindow } = require("electron");
const { spawn } = require("child_process");
const path = require("path");
const http = require("http");

const SERVER_PORT = 8756;
const PROJECT_ROOT = path.join(__dirname, ".."); // папка winbuddy (где main.py)

let pyProc = null;
let win = null;

// Путь к python из venv. На Windows — .venv\Scripts\python.exe
function venvPython() {
  if (process.platform === "win32") {
    return path.join(PROJECT_ROOT, ".venv", "Scripts", "python.exe");
  }
  return path.join(PROJECT_ROOT, ".venv", "bin", "python");
}

function startPythonServer() {
  const py = venvPython();
  console.log("Запускаю Python-сервер:", py);
  pyProc = spawn(py, ["-m", "winbuddy.agent.server"], {
    cwd: PROJECT_ROOT,
    env: { ...process.env },
  });
  pyProc.stdout.on("data", (d) => console.log("[py]", d.toString()));
  pyProc.stderr.on("data", (d) => console.log("[py]", d.toString()));
  pyProc.on("exit", (code) => console.log("Python-сервер завершился, код", code));
}

// Пингуем /health, пока сервер не ответит (или не выйдет таймаут).
function waitForServer(retries = 40) {
  return new Promise((resolve, reject) => {
    const tryOnce = (left) => {
      const req = http.get(
        { host: "127.0.0.1", port: SERVER_PORT, path: "/health", timeout: 500 },
        (res) => {
          if (res.statusCode === 200) resolve();
          else retry(left);
        }
      );
      req.on("error", () => retry(left));
      req.on("timeout", () => {
        req.destroy();
        retry(left);
      });
    };
    const retry = (left) => {
      if (left <= 0) return reject(new Error("сервер не поднялся"));
      setTimeout(() => tryOnce(left - 1), 300);
    };
    tryOnce(retries);
  });
}

function createWindow() {
  win = new BrowserWindow({
    width: 900,
    height: 720,
    minWidth: 620,
    minHeight: 480,
    backgroundColor: "#1a1d23",
    title: "winbuddy",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  win.setMenuBarVisibility(false);
  win.loadFile(path.join(__dirname, "index.html"));
}

app.whenReady().then(async () => {
  startPythonServer();
  try {
    await waitForServer();
    console.log("Сервер готов, открываю окно.");
  } catch (e) {
    console.error("Не дождались сервера:", e.message);
  }
  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

// Гасим Python при выходе.
function killPython() {
  if (pyProc && !pyProc.killed) {
    pyProc.kill();
    pyProc = null;
  }
}

app.on("window-all-closed", () => {
  killPython();
  if (process.platform !== "darwin") app.quit();
});
app.on("before-quit", killPython);
