const CATEGORIES = [
  ["food", "Food"],
  ["transport", "Transport"],
  ["shopping", "Shopping"],
  ["bills", "Bills"],
  ["entertainment", "Entertainment"],
  ["health", "Health"],
  ["travel", "Travel"],
  ["other", "Other"],
];

const COLORS = ["#e4c48a", "#d7efe2", "#f3d7c4", "#e38b78", "#c7c1ef", "#9fd0c8", "#f0e2b6", "#b9b2a8"];

const money = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const categoryName = Object.fromEntries(CATEGORIES);

const state = { category: "food", editing: null };

const $ = (sel) => document.querySelector(sel);

function todayISO() {
  const now = new Date();
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 10);
}

function greet() {
  const hour = new Date().getHours();
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

function toast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => node.classList.remove("show"), 2800);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data.detail;
    const message = typeof detail === "string" ? detail : "Something went wrong";
    throw new Error(message);
  }
  return data;
}

function showView(name) {
  document.querySelectorAll(".view").forEach((view) => {
    view.classList.toggle("hidden", view.id !== `view-${name}`);
  });
  document.querySelectorAll(".nav").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === name);
  });
  const titles = { overview: "Overview", ledger: "Ledger", wallets: "Wallets" };
  $("#page-title").textContent = titles[name];
}

function renderSummary(summary) {
  $("#total").textContent = money.format(summary.total);
  $("#cash").textContent = money.format(summary.cash);
  $("#credit").textContent = money.format(summary.credit);
  $("#cash-month").textContent = `${money.format(summary.month_cash)} spent this month`;
  $("#credit-month").textContent = `${money.format(summary.month_credit)} spent this month`;
  $("#month-total").textContent = money.format(summary.month_spent);
  $("#month-line").textContent = summary.month_spent
    ? `${money.format(summary.month_spent)} spent this month`
    : "Nothing spent this month yet.";

  const slices = summary.categories;
  const total = slices.reduce((sum, item) => sum + item.total, 0);
  if (!total) {
    $("#donut").style.background = "conic-gradient(rgba(246,241,231,0.08) 0 100%)";
    $("#legend").innerHTML = `<li class="empty">Categories appear after the first expense.</li>`;
  } else {
    let cursor = 0;
    const stops = slices.map((item, index) => {
      const start = cursor;
      cursor += (item.total / total) * 100;
      return `${COLORS[index % COLORS.length]} ${start}% ${cursor}%`;
    });
    $("#donut").style.background = `conic-gradient(${stops.join(",")})`;
    $("#legend").innerHTML = slices.map((item, index) => `
      <li>
        <span><i class="swatch" style="background:${COLORS[index % COLORS.length]}"></i>${categoryName[item.category]}</span>
        <span>${money.format(item.total)}</span>
      </li>
    `).join("");
  }

  const peak = Math.max(...summary.daily.map((day) => day.total), 1);
  $("#bars").innerHTML = summary.daily.map((day) => {
    const height = Math.max(4, (day.total / peak) * 140);
    const label = day.date.slice(8);
    return `<div class="bar" style="height:${height}px" title="${day.date}: ${money.format(day.total)}"><span>${label}</span></div>`;
  }).join("");

  const labels = {
    expense: "Expense",
    expense_reversal: "Expense returned",
    add: "Added",
    subtract: "Removed",
    set: "Balance set",
    transfer: "Transfer",
  };
  $("#activity").innerHTML = summary.activity.length
    ? summary.activity.map((item) => {
        const sign = item.delta < 0 ? "down" : "up";
        const account = item.account === "cash" ? "Cash" : "Card";
        return `<li>
          <div>
            <strong>${labels[item.kind] || item.kind}</strong>
            <div class="meta">${account}${item.note ? ` · ${item.note}` : ""}</div>
          </div>
          <div class="amount ${sign}">${item.delta > 0 ? "+" : ""}${money.format(item.delta)}</div>
        </li>`;
      }).join("")
    : `<li class="empty">Balance changes and expenses will show up here.</li>`;
}

function renderLedger(expenses) {
  const root = $("#ledger");
  if (!expenses.length) {
    root.innerHTML = `<p class="empty">No expenses match this view.</p>`;
    return;
  }
  root.innerHTML = expenses.map((item) => `
    <article class="expense">
      <div>
        <h3>${item.note || categoryName[item.category]}</h3>
        <div class="tags">
          <span class="tag">${categoryName[item.category]}</span>
          <span class="tag">${item.method === "cash" ? "Cash" : "Credit card"}</span>
          <span class="meta">${item.spent_on}</span>
        </div>
      </div>
      <strong>${money.format(item.amount)}</strong>
      <div class="row-actions">
        <button class="ghost" type="button" data-edit="${item.id}">Edit</button>
        <button class="danger" type="button" data-delete="${item.id}">Delete</button>
      </div>
    </article>
  `).join("");
  root.querySelectorAll("[data-edit]").forEach((button) => {
    button.addEventListener("click", () => openEditor(expenses.find((item) => item.id === Number(button.dataset.edit))));
  });
  root.querySelectorAll("[data-delete]").forEach((button) => {
    button.addEventListener("click", () => removeExpense(Number(button.dataset.delete)));
  });
}

async function refresh() {
  const [summary, expenses] = await Promise.all([
    api("/api/summary"),
    api(`/api/expenses?${filterQuery()}`),
  ]);
  renderSummary(summary);
  renderLedger(expenses);
}

function filterQuery() {
  const params = new URLSearchParams();
  const category = $("#filter-category").value;
  const method = $("#filter-method").value;
  const q = $("#search").value.trim();
  if (category) params.set("category", category);
  if (method) params.set("method", method);
  if (q) params.set("q", q);
  return params.toString();
}

function buildCategories() {
  const chips = $("#category-chips");
  chips.innerHTML = CATEGORIES.map(([id, label]) => `
    <button type="button" class="chip-btn${id === state.category ? " active" : ""}" data-category="${id}">${label}</button>
  `).join("");
  chips.querySelectorAll("[data-category]").forEach((button) => {
    button.addEventListener("click", () => {
      state.category = button.dataset.category;
      chips.querySelectorAll(".chip-btn").forEach((chip) => chip.classList.toggle("active", chip === button));
    });
  });
  const filter = $("#filter-category");
  CATEGORIES.forEach(([id, label]) => {
    const option = document.createElement("option");
    option.value = id;
    option.textContent = label;
    filter.append(option);
  });
}

function openEditor(expense) {
  const form = $("#expense-form");
  const fields = form.elements;
  state.editing = expense ? expense.id : null;
  $("#dialog-title").textContent = expense ? "Edit expense" : "Record expense";
  fields.amount.value = expense ? expense.amount : "";
  fields.note.value = expense ? expense.note : "";
  fields.spent_on.value = expense ? expense.spent_on : todayISO();
  fields.method.value = expense ? expense.method : "cash";
  state.category = expense ? expense.category : state.category;
  $("#category-chips").querySelectorAll(".chip-btn").forEach((chip) => {
    chip.classList.toggle("active", chip.dataset.category === state.category);
  });
  $("#expense-dialog").showModal();
}

async function removeExpense(id) {
  if (!confirm("Delete this expense and return the money to its account?")) return;
  try {
    await api(`/api/expenses/${id}`, { method: "DELETE" });
    toast("Expense removed and balance restored");
    await refresh();
  } catch (error) {
    toast(error.message);
  }
}

$("#greeting").textContent = greet();
buildCategories();
$("#expense-form").elements.spent_on.value = todayISO();

document.querySelectorAll(".nav").forEach((button) => {
  button.addEventListener("click", () => showView(button.dataset.view));
});
$("#open-expense").addEventListener("click", () => openEditor(null));
$("#close-dialog").addEventListener("click", () => $("#expense-dialog").close());

$("#filters").addEventListener("submit", (event) => event.preventDefault());

$("#expense-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const data = new FormData(form);
  const payload = {
    amount: Number(data.get("amount")),
    category: state.category,
    method: data.get("method"),
    note: String(data.get("note") || "").trim(),
    spent_on: data.get("spent_on"),
  };
  try {
    if (state.editing) {
      await api(`/api/expenses/${state.editing}`, { method: "PUT", body: JSON.stringify(payload) });
      toast("Expense updated");
    } else {
      await api("/api/expenses", { method: "POST", body: JSON.stringify(payload) });
      toast("Expense recorded");
    }
    $("#expense-dialog").close();
    form.reset();
    form.elements.spent_on.value = todayISO();
    await refresh();
  } catch (error) {
    toast(error.message);
  }
});

$("#filters").addEventListener("input", () => {
  refresh().catch((error) => toast(error.message));
});

$("#adjust-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const data = new FormData(form);
  const payload = {
    account: data.get("account"),
    action: data.get("action"),
    amount: Number(data.get("amount")),
    note: String(data.get("note") || "").trim(),
  };
  try {
    await api("/api/wallet", { method: "POST", body: JSON.stringify(payload) });
    form.reset();
    toast("Balance updated");
    await refresh();
  } catch (error) {
    toast(error.message);
  }
});

$("#transfer-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  try {
    const data = new FormData(form);
    await api("/api/transfer", {
      method: "POST",
      body: JSON.stringify({
        source: data.get("source"),
        amount: Number(data.get("amount")),
        note: String(data.get("note") || "").trim(),
      }),
    });
    form.reset();
    toast("Transfer complete");
    await refresh();
  } catch (error) {
    toast(error.message);
  }
});

refresh().catch((error) => toast(error.message));
