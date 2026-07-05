// Renderer: логика окна.
//
// Держит websocket-соединение с Python-сервером, отправляет задачи, рисует
// поток событий агента и — главное — обрабатывает подтверждение write-действий
// через кнопки «Выполнить / Отмена», отправляя ответ обратно на сервер.

const PORT = window.winbuddy?.serverPort || 8756;
const WS_URL = `ws://127.0.0.1:${PORT}/ws`;

const logEl = document.getElementById("log");
const inputEl = document.getElementById("input");
const sendEl = document.getElementById("send");
const dotEl = document.getElementById("statusDot");
const statusEl = document.getElementById("statusText");

let ws = null;
let busy = false; // агент занят ходом — блокируем ввод

function connect() {
  ws = new WebSocket(WS_URL);

  ws.onopen = () => {
    dotEl.classList.add("online");
    statusEl.textContent = "готов";
  };
  ws.onclose = () => {
    dotEl.classList.remove("online");
    statusEl.textContent = "нет связи — переподключение…";
    setTimeout(connect, 1500);
  };
  ws.onerror = () => {
    statusEl.textContent = "ошибка соединения";
  };
  ws.onmessage = (ev) => handleEvent(JSON.parse(ev.data));
}

function scrollDown() {
  logEl.scrollTop = logEl.scrollHeight;
}

function addUser(text) {
  const el = document.createElement("div");
  el.className = "msg user";
  el.textContent = text;
  logEl.appendChild(el);
  scrollDown();
}

function addBot(text) {
  const el = document.createElement("div");
  el.className = "msg bot";
  el.textContent = text;
  logEl.appendChild(el);
  scrollDown();
}

function addStep(name, input) {
  const el = document.createElement("div");
  el.className = "msg step";
  const args = input ? ` <span class="args">${JSON.stringify(input)}</span>` : "";
  el.innerHTML = `🔍 ${name}${args}`;
  logEl.appendChild(el);
  scrollDown();
}

function addResult(result) {
  const el = document.createElement("div");
  el.className = "result-ok";
  el.textContent = "✓ " + (result.note || JSON.stringify(result));
  logEl.appendChild(el);
  scrollDown();
}

// Сигнатурный элемент: панель подтверждения (шлагбаум).
function addGuardrail(name, plan) {
  const card = document.createElement("div");
  card.className = "guardrail";

  const title = document.createElement("div");
  title.className = "title";
  title.textContent = "⚠ Подтверждение действия — пока ничего не сделано";
  card.appendChild(title);

  const pre = document.createElement("pre");
  pre.textContent = JSON.stringify(plan, null, 2);
  card.appendChild(pre);

  const actions = document.createElement("div");
  actions.className = "actions";

  const approve = document.createElement("button");
  approve.className = "btn-approve";
  approve.textContent = "Выполнить";

  const reject = document.createElement("button");
  reject.className = "btn-reject";
  reject.textContent = "Отмена";

  const decide = (approved) => {
    ws.send(JSON.stringify({ approved }));
    card.classList.add("decided");
    const label = document.createElement("span");
    label.className = "decision-label";
    label.style.color = approved ? "var(--warn)" : "var(--muted)";
    label.textContent = approved ? "→ подтверждено" : "→ отменено";
    actions.after(label);
  };

  approve.onclick = () => decide(true);
  reject.onclick = () => decide(false);
  actions.appendChild(approve);
  actions.appendChild(reject);
  card.appendChild(actions);

  logEl.appendChild(card);
  scrollDown();
}

function setBusy(state) {
  busy = state;
  sendEl.disabled = state;
  inputEl.disabled = state;
  statusEl.textContent = state ? "думаю…" : "готов";
}

function handleEvent(ev) {
  switch (ev.type) {
    case "text":
      addBot(ev.text);
      break;
    case "tool_call":
      addStep(ev.name, ev.input);
      break;
    case "plan":
      // план приходит вместе с confirm_request; сам план покажем в guardrail
      break;
    case "confirm_request":
      addGuardrail(ev.name, ev.plan);
      break;
    case "result":
      addResult(ev.result);
      break;
    case "rejected":
      // уже показали метку в guardrail
      break;
    case "error":
      addBot("⚠️ " + ev.text);
      break;
    case "final":
      addBot(ev.text);
      setBusy(false);
      break;
    case "reset_ok":
      addBot("Контекст очищен.");
      setBusy(false);
      break;
  }
}

function send() {
  const text = inputEl.value.trim();
  if (!text || busy || !ws || ws.readyState !== WebSocket.OPEN) return;

  if (text.toLowerCase() === "reset" || text.toLowerCase() === "сброс") {
    ws.send(JSON.stringify({ type: "reset" }));
    addUser(text);
    inputEl.value = "";
    return;
  }

  addUser(text);
  ws.send(JSON.stringify({ type: "task", text }));
  inputEl.value = "";
  setBusy(true);
}

sendEl.onclick = send;
inputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    send();
  }
});

connect();
