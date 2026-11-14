import { CarrefourAccountPanel } from "./carrefour-account.js?v=0.88.5";
class DiaAccountPanel extends CarrefourAccountPanel {
  get providerId(){return "dia";}
  get providerName(){return "DIA";}
  async _action(action,fields){await super._action(action,fields);if(action==="login"&&this._status.connected)this.shadowRoot.querySelector("#login-dialog").close();}
}
if(!customElements.get("isl-dev-dia-account"))customElements.define("isl-dev-dia-account",DiaAccountPanel);
