/**
 * 个微原句案例：页面里只写会话 id 与北京时间窗，打开页面时从 wecom-chat 取那一小段原句渲染。
 *
 * 为什么不直接写进 HTML：本仓是 PUBLIC 仓库，客户原句一旦提交就永久留在 git 历史里；
 * 写进「」的引语还要过 quote-guard 逐字核验。这里取到的文字天然逐字来自消息库，
 * 并且已在服务端打码（手机号 / 客户编码与备注称呼 / 住址 / 微信号 / 团队真名），不返回客户名。
 *
 * 用法（会话 id 从团队会话页 https://wecom-chat.vercel.app/team/weixin/<id> 的地址栏复制，时间照页面上的分钟写）：
 *   <figure class="wx-quote" data-c="<会话UUID>" data-from="2026-10-14 13:32" data-to="2026-10-14 13:34"
 *           data-title="客户要求重新算一遍"></figure>
 *   <script src="/akke/wx-quote.js?v=3" defer></script>
 * 时间窗闭区间、到 to 那一分钟的 59 秒为止，最长 3 天、最多 30 条。
 * 请求带上本页路径 p：后端只放行本页上真写着的 (会话, from, to)，换个窗口取不到——所以时间窗要写在页面源码里，不能由脚本拼出来。
 * 改本文件要同时把引用处的 ?v= 加 1：*.js 在 vercel.json 里是一年 immutable 缓存。
 * 后端：Akke-AI/wecom-chat src/app/api/public/weixin-quotes。
 */
(function () {
  // 走 upio.ai 同域转发（vercel.json rewrites）：国内网络连不上 *.vercel.app，直连会报 Failed to fetch
  var API = "/api/weixin-quotes";
  var CSS =
    ".wx-quote{margin:18px 0 6px;border:1px solid var(--line,#dce4df);border-radius:16px;background:#f2f4f1;overflow:hidden}" +
    ".wx-quote figcaption{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;padding:10px 14px;background:white;border-bottom:1px solid var(--line,#dce4df);font-size:13px}" +
    ".wx-quote figcaption strong{font-size:14px}.wx-quote figcaption span{color:var(--muted,#61706e)}" +
    ".wx-quote .wx-flow{padding:12px 14px 14px;display:flex;flex-direction:column;gap:8px}" +
    ".wx-quote .wx-t{align-self:center;font-size:12px;color:var(--muted,#61706e)}" +
    ".wx-quote .wx-row{display:flex;gap:8px;align-items:flex-start;max-width:86%}" +
    ".wx-quote .wx-row.out{align-self:flex-end;flex-direction:row-reverse}" +
    ".wx-quote .wx-av{flex:none;width:34px;height:34px;border-radius:6px;background:#c9d3cf;color:#31423f;font-size:11px;display:grid;place-items:center}" +
    ".wx-quote .wx-row.out .wx-av{background:var(--green,#087763);color:white}" +
    ".wx-quote .wx-b{background:white;border-radius:8px;padding:8px 11px;font-size:15px;line-height:1.55;white-space:pre-wrap;word-break:break-word}" +
    ".wx-quote .wx-row.out .wx-b{background:#a9ea7a}" +
    ".wx-quote .wx-msg{padding:14px;font-size:13px;color:var(--muted,#61706e)}";

  function bjt(s, end) {
    var m = /^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})$/.exec((s || "").trim());
    return m ? m[1] + "T" + m[2] + (end ? ":59.999" : ":00") + "+08:00" : "";
  }
  function label(iso) {
    var d = new Date(new Date(iso).getTime() + 8 * 3600e3);
    var p = function (n) { return String(n).padStart(2, "0"); };
    return d.getUTCMonth() + 1 + "月" + d.getUTCDate() + "日 " + p(d.getUTCHours()) + ":" + p(d.getUTCMinutes());
  }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function render(box) {
    var from = bjt(box.dataset.from, false);
    var to = bjt(box.dataset.to, true);
    box.textContent = "";
    var cap = el("figcaption");
    cap.appendChild(el("strong", "", box.dataset.title || "原句片段"));
    var meta = el("span", "", "原句 · 读取中…");
    cap.appendChild(meta);
    box.appendChild(cap);
    var flow = el("div", "wx-flow");
    box.appendChild(flow);
    var fail = function (why) {
      meta.textContent = "原句 · 未取到";
      flow.replaceWith(el("p", "wx-msg", "原句暂时取不到（" + why + "）。上面的转述仍然有效；需要逐条核对请到团队会话页查看。"));
    };
    if (!box.dataset.c || !from || !to) return fail("页面里的会话或时间写法不对");
    var url = API + "?c=" + encodeURIComponent(box.dataset.c) + "&from=" + encodeURIComponent(from) + "&to=" + encodeURIComponent(to) +
      "&p=" + encodeURIComponent(location.pathname);
    fetch(url, { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(new Error("HTTP " + r.status)); })
      .then(function (data) {
        if (!data.messages || !data.messages.length) return fail("这个时间窗里没有已入库的消息");
        meta.textContent = "原句 · 会话 " + data.conversation + " · 已打码" + (data.truncated ? " · 仅显示前 30 条" : "");
        var last = "";
        data.messages.forEach(function (m) {
          var t = label(m.at);
          if (t !== last) flow.appendChild(el("div", "wx-t", t));
          last = t;
          var row = el("div", "wx-row" + (m.role === "sales" ? " out" : ""));
          row.appendChild(el("div", "wx-av", m.role === "sales" ? "销售" : "客户"));
          row.appendChild(el("div", "wx-b", m.text));
          flow.appendChild(row);
        });
      })
      .catch(function (e) { fail(e && e.message ? e.message : "网络错误"); });
  }

  function init() {
    var boxes = document.querySelectorAll("figure.wx-quote[data-c]");
    if (!boxes.length) return;
    if (!document.getElementById("wx-quote-css")) {
      var st = el("style");
      st.id = "wx-quote-css";
      st.textContent = CSS;
      document.head.appendChild(st);
    }
    boxes.forEach(render);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
