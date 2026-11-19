import { installDialogCloseButtons } from "./dialog-controls.js?v=0.90.4";
import { installToggleStyles } from "./toggle-styles.js?v=0.90.4";

export const MERCADONA_LINKS = Object.freeze({
  gmailSignup: "https://accounts.google.com/signup",
  twoStep: "https://myaccount.google.com/signinoptions/twosv",
  appPasswords: "https://myaccount.google.com/apppasswords",
  whatsapp: "https://wa.me/34683772144?text=Quiero%20modificar%20mi%20email",
  conditions: "https://privacyapps.mercadona.com/CondicionesTicketDigital.pdf",
  shop: "https://tienda.mercadona.es/",
});

const STEPS = ["Correo nuevo", "Contraseña de aplicación", "Avisar a Mercadona", "Conectar"];

// Mercadona account: online shop and digital tickets are independent and optional.
// Passwords are only sent to Home Assistant; they are never shown again.
export class MercadonaAccountPanel extends HTMLElement {
  constructor(){super();this.attachShadow({mode:"open"});this._status={};this._generation=0;this._step=1;}
  set hass(value){this._hass=value;if(!this._built)this._build();this._render();}
  disconnectedCallback(){this.deactivate();}
  deactivate(){this._generation++;this._busy=false;this._syncing=false;if(!this._built)return;this.shadowRoot.querySelectorAll("dialog").forEach(d=>d.close());this.shadowRoot.querySelectorAll("input[type=password],input[type=email]").forEach(i=>i.value="");}
  async activate(){
    this._generation++;const generation=this._generation;this._error="";this._notice="";
    try{const result=await this._call("status");if(generation!==this._generation)return;this._status=result;}
    catch(error){if(generation===this._generation)this._error=error.message??"No se ha podido consultar la cuenta de Mercadona.";}
    this._render();
  }
  _call(action,fields={}){return this._hass.callWS({type:"improved_shopping_list_dev/mercadona_account/"+action,...fields});}
  _build(){
    this._built=true;const link=(href,text,primary=false)=>`<a class="link${primary?" primary-link":""}" href="${href}" target="_blank" rel="noopener noreferrer">${text}</a>`;
    this.shadowRoot.innerHTML=`<style>
      :host{display:block;font:inherit;color:inherit}*{box-sizing:border-box}[hidden]{display:none!important}
      button,input,select{font:inherit;color:inherit;min-height:42px;border:1px solid var(--divider-color,#d9e0dd);border-radius:10px;padding:10px 14px;background:var(--card-background-color,#fff);min-width:0}button{cursor:pointer}button:disabled{opacity:.5;cursor:default}
      button.primary,.primary-link{background:var(--isl-dev-green,#267052);color:#fff;border:0}.danger{background:#d32f2f;color:#fff;border:0}
      .actions{display:flex;gap:10px;flex-wrap:wrap;justify-content:flex-end;margin-top:16px}.wizard-back{margin-right:auto}
      dialog{position:fixed;inset:0;border:0;border-radius:18px;background:var(--card-background-color,#fff);color:inherit;width:min(650px,calc(100vw - 24px));max-height:90vh;padding:24px;overflow:auto}dialog::backdrop{background:#0008}
      h2{font-size:20px;margin:0 40px 18px 0}h3{font-size:16px;margin:0}p,li{font-size:14px;line-height:1.5}.note{opacity:.7}.error{color:var(--error-color,#d32f2f);overflow-wrap:anywhere}
      label{display:flex;flex-direction:column;gap:7px;margin-top:16px}input,select{width:100%}
      .source{border:1px solid var(--divider-color,#d9e0dd);border-radius:14px;padding:16px;margin-top:16px}.source-head{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.source-state{font-size:13px;opacity:.75;margin:6px 0 0;overflow-wrap:anywhere}.source .actions{margin-top:12px}
      .warning{border-left:4px solid #f0a020;background:#f0a0201a;border-radius:10px;padding:12px 14px;margin:12px 0}
      .link{display:inline-block;padding:10px 14px;border-radius:10px;text-decoration:none;color:var(--primary-color,#168aec);border:1px solid var(--divider-color,#d9e0dd);margin:4px 8px 4px 0}.link.primary-link{background:var(--isl-dev-green,#267052);color:#fff;border-color:transparent;font-weight:500}
      .wizard-steps{display:flex;gap:8px;margin:0 0 20px;padding:0;list-style:none}.wizard-steps li{flex:1;padding:8px;border-radius:10px;font-size:12px;line-height:1.3;background:var(--secondary-background-color,#64748b22)}.wizard-steps li[aria-current="step"]{background:#168aec;color:#fff}.wizard-steps li.done{background:var(--isl-dev-green,#267052);color:#fff}
      ol.instructions{padding-left:22px}ol.instructions li{margin-bottom:8px}
      .account-options{display:grid;gap:14px;margin-top:8px}.account-options label{flex-direction:row;align-items:center;justify-content:space-between;gap:16px;margin:0}.option-help{font-size:12px;line-height:1.5;opacity:.7;margin:5px 0 0}
      .account-options input{appearance:none;width:38px;height:22px;min-height:22px;flex:none;border:0;border-radius:20px;padding:3px;background:var(--switch-unchecked-track-color,#64748b);cursor:pointer}.account-options input:before{content:"";display:block;width:16px;height:16px;border-radius:50%;background:#fff;transition:transform .15s}.account-options input:checked{background:var(--isl-dev-green,#267052)}.account-options input:checked:before{transform:translateX(16px)}.account-options input:disabled{opacity:.45;cursor:default}
      .server{display:grid;grid-template-columns:2fr 1fr;gap:10px}@media(max-width:500px){dialog{padding:18px}.wizard-steps li{font-size:11px;padding:6px}.server{grid-template-columns:1fr}}
    </style>
    <h3>Cuenta</h3>
    <p class="note">Conecta la tienda online, el ticket digital o ambos. Cada opción funciona por separado.</p>
    <div class="account-options"><div><label>Recuperar compras de los últimos 12 meses<input id="history-enabled" type="checkbox" role="switch" aria-describedby="history-help"></label><p id="history-help" class="option-help">Incorpora a Analítica → Compras las fechas e importes de los tickets digitales de los últimos 12 meses. Revisar sus tickets es opcional.</p></div>
      <div><label>Descargar ticket automáticamente<input id="auto-ticket" type="checkbox" role="switch" aria-describedby="auto-help"></label><p id="auto-help" class="option-help">Después de marcar productos como comprados, revisa el buzón como máximo una vez al día y guarda el último ticket pendiente de revisión, sin modificar stock ni precios.</p></div></div>
    <section class="source" aria-labelledby="mail-title"><div class="source-head"><h3 id="mail-title">Ticket digital (compras en tienda)</h3></div><p id="mail-state" class="source-state"></p>
      <div class="actions"><button id="mail-disconnect" type="button" class="danger" hidden>Desconectar</button><button id="mail-setup" type="button" class="primary">Configurar ticket digital</button></div></section>
    <section class="source" aria-labelledby="online-title"><div class="source-head"><h3 id="online-title">Tienda online (tienda.mercadona.es)</h3></div><p id="online-state" class="source-state"></p>
      <div class="actions"><button id="online-refresh" type="button" hidden>Consultar pedidos</button><button id="online-disconnect" type="button" class="danger" hidden>Desconectar</button><button id="online-connect" type="button" class="primary">Conectar tienda online</button></div></section>
    <p id="status-error" class="error" role="alert" hidden></p><p id="status-note" role="status"></p>
    <dialog id="mail-dialog" aria-labelledby="mail-dialog-title"><h2 id="mail-dialog-title">Ticket digital de Mercadona</h2>
      <ol class="wizard-steps" aria-label="Pasos de configuración">${STEPS.map((label,index)=>`<li data-step-label="${index+1}">${index+1}. ${label}</li>`).join("")}</ol>
      <section data-step="1"><h3>Usa un correo solo para los tickets</h3>
        <div class="warning"><strong>No uses tu correo personal.</strong> Home Assistant guardará el correo y su contraseña de aplicación para leer los tickets. Crea una cuenta de Gmail nueva que solo reciba los tickets de Mercadona: así no se expone tu buzón principal.</div>
        <ol class="instructions"><li>Crea la cuenta nueva con el botón de abajo y apunta la dirección.</li><li>No la uses para nada más: la integración solo lee los correos de Mercadona y nunca los marca, mueve ni borra.</li></ol>
        <p>${link(MERCADONA_LINKS.gmailSignup,"Crear cuenta de Gmail",true)}${link(MERCADONA_LINKS.conditions,"Condiciones del Ticket Digital (PDF)")}</p>
        <div class="actions"><button type="button" class="primary" data-next="2">Ya tengo el correo nuevo</button></div></section>
      <section data-step="2" hidden><h3>Crea una contraseña de aplicación</h3>
        <p>Gmail no permite usar la contraseña normal para leer el correo desde Home Assistant. Necesitas una <strong>contraseña de aplicación</strong> de 16 letras.</p>
        <ol class="instructions"><li>Inicia sesión con el correo nuevo y activa la <strong>verificación en dos pasos</strong>.</li><li>Abre <strong>Contraseñas de aplicación</strong>, escribe un nombre como «Home Assistant» y pulsa <strong>Crear</strong>.</li><li>Copia la contraseña que aparece. La pegarás en el último paso; no hace falta guardarla en otro sitio.</li></ol>
        <p>${link(MERCADONA_LINKS.twoStep,"Verificación en dos pasos")}${link(MERCADONA_LINKS.appPasswords,"Contraseñas de aplicación",true)}</p>
        <p class="note">Si usas otro proveedor (Outlook, iCloud…), consulta en su ayuda cómo activar IMAP y crear una contraseña de aplicación.</p>
        <div class="actions"><button type="button" class="wizard-back" data-back="1">Volver</button><button type="button" class="primary" data-next="3">Ya tengo la contraseña</button></div></section>
      <section data-step="3" hidden><h3>Pide a Mercadona que envíe los tickets al correo nuevo</h3>
        <ol class="instructions"><li>Pulsa <strong>Abrir WhatsApp de Mercadona</strong>. Se abrirá el chat con el mensaje «Quiero modificar mi email».</li><li>Envíalo. Carol, el asistente de Mercadona, te preguntará si quieres cambiar el correo y te pedirá la nueva dirección: escribe el correo nuevo.</li><li>Confirma el cambio si Mercadona te lo pide.</li></ol>
        <p>${link(MERCADONA_LINKS.whatsapp,"Abrir WhatsApp de Mercadona",true)}</p>
        <p class="note">También puedes abrir uno de los correos de tickets que ya recibes y pulsar «Actualiza el correo donde recibes tus tickets»: abre el mismo chat. Solo llegan por correo las compras pagadas con la tarjeta asociada al Ticket Digital.</p>
        <div class="actions"><button type="button" class="wizard-back" data-back="2">Volver</button><button type="button" class="primary" data-next="4">Ya he cambiado el correo</button></div></section>
      <section data-step="4" hidden><h3>Conecta el buzón</h3>
        <form id="mail-form"><label>Proveedor<select id="mail-provider"><option value="gmail">Gmail</option><option value="other">Otro proveedor (IMAP)</option></select></label>
          <label>Correo que recibe los tickets<input id="mail-username" type="email" autocomplete="username" maxlength="255" required></label>
          <label>Contraseña de aplicación<input id="mail-password" type="password" autocomplete="new-password" maxlength="255"></label><p id="mail-password-note" class="option-help"></p>
          <div id="mail-server" hidden><div class="server"><label>Servidor IMAP<input id="mail-host" type="text" maxlength="253" placeholder="imap.ejemplo.com" autocomplete="off"></label><label>Puerto<input id="mail-port" type="number" min="1" max="65535" value="993"></label></div><label>Carpeta<input id="mail-folder" type="text" maxlength="200" value="INBOX" autocomplete="off"></label><p class="option-help">Conexión cifrada (SSL/TLS). La carpeta se abre en solo lectura.</p></div>
          <p class="note">Solo se leen los correos de Mercadona con un ticket en PDF. No se marcan como leídos, no se mueven y no se borran.</p>
          <div class="actions"><button type="button" class="wizard-back" data-back="3">Volver</button><button class="primary" type="submit">Probar y conectar</button></div></form></section>
      <p id="mail-error" class="error" role="alert" hidden></p><p id="mail-progress" role="status"></p>
    </dialog>
    <dialog id="online-dialog" aria-labelledby="online-dialog-title"><h2 id="online-dialog-title">Tienda online de Mercadona</h2>
      <div class="warning">Mercadona no ofrece una API pública. La conexión usa la misma API privada que su web; puede dejar de funcionar si Mercadona la cambia. Solo se consultan tus pedidos: nunca se modifica el carrito ni se hacen pedidos.</div>
      <p>La contraseña se usa una sola vez para iniciar sesión y <strong>no se guarda</strong>. Home Assistant conserva solo el permiso renovable de la sesión; si caduca, tendrás que volver a conectar.</p>
      <p>${link(MERCADONA_LINKS.shop,"Abrir tienda.mercadona.es")}</p>
      <form id="online-form"><label>Correo de la tienda online<input id="online-username" type="email" autocomplete="username" maxlength="255" required></label><label>Contraseña<input id="online-password" type="password" autocomplete="current-password" maxlength="255" required></label>
        <div class="actions"><button class="primary" type="submit">Conectar</button></div></form>
      <p id="online-error" class="error" role="alert" hidden></p><p id="online-progress" role="status"></p>
    </dialog>`;
    const r=this.shadowRoot;installDialogCloseButtons(r);installToggleStyles(r);
    r.querySelectorAll("dialog").forEach(d=>d.addEventListener("cancel",e=>{if(this._busy)e.preventDefault();}));
    r.querySelector("#mail-dialog").addEventListener("close",()=>{r.querySelector("#mail-password").value="";});
    r.querySelector("#online-dialog").addEventListener("close",()=>{r.querySelector("#online-password").value="";});
    r.querySelectorAll("[data-next],[data-back]").forEach(button=>button.addEventListener("click",()=>this._showStep(Number(button.dataset.next||button.dataset.back))));
    r.querySelector("#mail-setup").addEventListener("click",()=>this._openMail());
    r.querySelector("#online-connect").addEventListener("click",()=>{this._dialogError="";r.querySelector("#online-username").value="";r.querySelector("#online-password").value="";this._render();r.querySelector("#online-dialog").showModal();});
    r.querySelector("#mail-disconnect").addEventListener("click",()=>void this._action("mail_disconnect"));
    r.querySelector("#online-disconnect").addEventListener("click",()=>void this._action("online_disconnect"));
    r.querySelector("#online-refresh").addEventListener("click",()=>void this._action("online_orders"));
    r.querySelector("#mail-provider").addEventListener("change",()=>this._render());
    r.querySelector("#mail-username").addEventListener("input",()=>this._render());
    for(const [id,key] of [["history-enabled","history_enabled"],["auto-ticket","auto_ticket"]])r.querySelector("#"+id).addEventListener("change",()=>void this._action("options",{options:{history_enabled:false,auto_ticket:false,...this._status.options,[key]:r.querySelector("#"+id).checked}}));
    r.querySelector("#mail-form").addEventListener("submit",event=>{event.preventDefault();const provider=r.querySelector("#mail-provider").value,password=r.querySelector("#mail-password").value;r.querySelector("#mail-password").value="";
      const fields={provider,username:r.querySelector("#mail-username").value.trim(),password};
      if(provider==="other")Object.assign(fields,{host:r.querySelector("#mail-host").value.trim(),port:Number(r.querySelector("#mail-port").value)||993,folder:r.querySelector("#mail-folder").value.trim()||"INBOX"});
      void this._action("mail_connect",fields,"#mail-dialog");});
    r.querySelector("#online-form").addEventListener("submit",event=>{event.preventDefault();const username=r.querySelector("#online-username").value.trim(),password=r.querySelector("#online-password").value;r.querySelector("#online-password").value="";void this._action("login",{username,password},"#online-dialog");});
  }
  _openMail(){
    const r=this.shadowRoot,mail=this._status.mail??{};this._dialogError="";
    r.querySelector("#mail-provider").value=mail.provider||"gmail";r.querySelector("#mail-username").value=mail.username||"";r.querySelector("#mail-password").value="";
    r.querySelector("#mail-host").value=mail.provider==="other"?mail.host||"":"";r.querySelector("#mail-port").value=String(mail.port||993);r.querySelector("#mail-folder").value=mail.folder||"INBOX";
    this._showStep(mail.connected?4:1);r.querySelector("#mail-dialog").showModal();
  }
  _showStep(step){
    this._step=step;const r=this.shadowRoot;
    r.querySelectorAll("#mail-dialog section[data-step]").forEach(section=>section.hidden=Number(section.dataset.step)!==step);
    r.querySelectorAll("#mail-dialog .wizard-steps li").forEach(item=>{const value=Number(item.dataset.stepLabel);item.classList.toggle("done",value<step);if(value===step)item.setAttribute("aria-current","step");else item.removeAttribute("aria-current");});
    this._render();
  }
  async _action(action,fields={},dialog=""){
    if(this._busy)return;const generation=this._generation;this._busy=true;this._error="";this._dialogError="";this._notice="";this._pending=action;this._render();
    try{
      const result=await this._call(action,fields);if(generation!==this._generation)return;this._status=result;
      if(dialog)this.shadowRoot.querySelector(dialog).close();
      this._notice=action==="mail_connect"?"Ticket digital conectado.":action==="login"?"Tienda online conectada.":"";
    }catch(error){if(generation===this._generation){const message=error.message??"No se ha podido conectar con Mercadona.";if(dialog)this._dialogError=message;else this._error=message;}}
    finally{if(generation===this._generation){this._busy=false;this._pending="";this._render();}}
  }
  _render(){
    if(!this._built)return;const r=this.shadowRoot,s=this._status,mail=s.mail??{},online=s.online??{},locked=this._busy||this._hass?.user?.is_admin!==true;
    r.querySelectorAll("button,input,select").forEach(el=>el.disabled=locked);
    for(const [id,key] of [["history-enabled","history_enabled"],["auto-ticket","auto_ticket"]]){const input=r.querySelector("#"+id);input.checked=Boolean(s.options?.[key]);input.disabled=locked||!s.connected;}
    r.querySelector("#mail-state").textContent=mail.connected?`Conectado: ${mail.username} · ${mail.tickets===1?"1 ticket encontrado":`${mail.tickets??0} tickets encontrados`}`:"Sin conectar. Lee los tickets en PDF que Mercadona envía al correo tras pagar con la tarjeta asociada.";
    r.querySelector("#mail-setup").textContent=mail.connected?"Cambiar conexión":"Configurar ticket digital";r.querySelector("#mail-disconnect").hidden=!mail.connected;
    const orders=online.orders;
    r.querySelector("#online-state").textContent=!online.connected?"Sin conectar. Consulta los pedidos de tu cuenta de tienda.mercadona.es.":orders==null?"Conectada. Pulsa Consultar pedidos para comprobar tu historial.":orders===0?"Conectada. Tu cuenta todavía no tiene pedidos online: cuando hagas uno aparecerá aquí.":`Conectada · ${orders===1?"1 pedido online":`${orders} pedidos online`}. La importación de sus tickets se activará cuando se verifique el formato del detalle.`;
    r.querySelector("#online-connect").hidden=Boolean(online.connected);r.querySelector("#online-disconnect").hidden=!online.connected;r.querySelector("#online-refresh").hidden=!online.connected;
    const other=r.querySelector("#mail-provider").value==="other";r.querySelector("#mail-server").hidden=!other;for(const id of ["mail-host","mail-port","mail-folder"])r.querySelector("#"+id).required=other;
    const reuse=Boolean(mail.connected&&r.querySelector("#mail-username").value.trim()===mail.username);const password=r.querySelector("#mail-password");password.required=!reuse;password.placeholder=reuse?"••••••••":"";
    r.querySelector("#mail-password-note").textContent=reuse?"Déjala vacía para mantener la contraseña guardada.":other?"":"Pega las 16 letras; los espacios se eliminan automáticamente.";
    r.querySelector("#mail-progress").textContent=this._pending==="mail_connect"?"Comprobando el buzón…":"";
    r.querySelector("#online-progress").textContent=this._pending==="login"?"Conectando con Mercadona…":"";
    r.querySelector("#status-note").textContent=this._pending==="online_orders"?"Consultando pedidos…":this._pending==="options"&&s.options?.history_enabled===false?"Buscando tickets en el correo…":this._notice||"";
    for(const [id,text] of [["status-error",this._error],["mail-error",this._dialogError],["online-error",this._dialogError]]){const node=r.querySelector("#"+id);node.textContent=text||"";node.hidden=!text;}
  }
}
if(!customElements.get("isl-dev-mercadona-account"))customElements.define("isl-dev-mercadona-account",MercadonaAccountPanel);
