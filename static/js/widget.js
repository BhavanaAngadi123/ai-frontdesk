/* FrontDesk embeddable chat widget.
   Usage on any site:
     <script src="https://YOUR-HOST/static/js/widget.js"
             data-business="demo" data-name="Demo Boutique"></script>
*/
(function(){
  const script = document.currentScript;
  const HOST = new URL(script.src).origin;
  const BUSINESS = script.dataset.business || 'demo';
  const NAME = script.dataset.name || 'Our shop';

  const css = `
  #fd-bubble{position:fixed;bottom:22px;right:22px;width:60px;height:60px;border-radius:50%;
    background:#1B1512;color:#F6F1E7;border:2px solid #1B1512;cursor:pointer;z-index:99998;
    font-size:26px;box-shadow:4px 4px 0 #E08A00;display:grid;place-items:center;font-family:Georgia,serif}
  #fd-panel{position:fixed;bottom:96px;right:22px;width:min(380px,calc(100vw - 44px));height:520px;
    max-height:calc(100vh - 130px);background:#FFFDF7;border:2px solid #1B1512;border-radius:18px;
    box-shadow:6px 6px 0 #1B1512;z-index:99999;display:none;flex-direction:column;overflow:hidden;
    font-family:'Space Grotesk',system-ui,sans-serif}
  #fd-panel.open{display:flex}
  #fd-head{background:#1B1512;color:#F6F1E7;padding:12px 16px;font-weight:700}
  #fd-head small{opacity:.7;font-weight:400}
  #fd-body{flex:1;overflow-y:auto;padding:14px;display:flex;flex-direction:column;gap:10px}
  #fd-body .m{max-width:85%;padding:9px 13px;border-radius:13px;font-size:14px;line-height:1.5;white-space:pre-wrap}
  #fd-body .a{background:#EFE7D4;border:1.5px solid #1B1512;border-bottom-left-radius:4px;align-self:flex-start;color:#1B1512}
  #fd-body .c{background:#1B1512;color:#F6F1E7;border-bottom-right-radius:4px;align-self:flex-end}
  #fd-form{display:flex;border-top:2px solid #1B1512}
  #fd-form input{flex:1;border:0;padding:12px 14px;font-size:14px;outline:none;background:transparent}
  #fd-form button{border:0;background:#E08A00;padding:0 18px;font-weight:700;cursor:pointer}`;

  const style = document.createElement('style'); style.textContent = css; document.head.appendChild(style);

  const bubble = document.createElement('button'); bubble.id = 'fd-bubble'; bubble.innerHTML = '✦'; bubble.title = 'Chat with us';
  const panel = document.createElement('div'); panel.id = 'fd-panel';
  panel.innerHTML = `<div id="fd-head">${NAME}<br><small>AI receptionist · replies instantly</small></div>
    <div id="fd-body"></div>
    <form id="fd-form"><input id="fd-in" placeholder="Type a message…" autocomplete="off"><button>Send</button></form>`;
  document.body.appendChild(bubble); document.body.appendChild(panel);

  const bodyEl = panel.querySelector('#fd-body');
  let convId = null;
  function add(cls, text){
    const d = document.createElement('div'); d.className = 'm ' + cls; d.textContent = text;
    bodyEl.appendChild(d); bodyEl.scrollTop = bodyEl.scrollHeight;
  }
  async function send(text){
    text = (text || '').trim(); if(!text) return;
    add('c', text); panel.querySelector('#fd-in').value = '';
    try{
      const r = await fetch(HOST + '/api/chat', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({business: BUSINESS, conversation_id: convId, text})});
      const j = await r.json(); convId = j.conversation_id;
      if (j.disclosure) add('a', j.disclosure);
      add('a', j.reply + (j.demo_mode ? '\n—' : ''));
    }catch(e){ add('a', 'Sorry — connection hiccup. Please try again.'); }
  }
  bubble.onclick = () => { panel.classList.toggle('open'); if(panel.classList.contains('open') && !convId) send('Hi!'); };
  panel.querySelector('#fd-form').onsubmit = e => { e.preventDefault(); send(panel.querySelector('#fd-in').value); };
})();
