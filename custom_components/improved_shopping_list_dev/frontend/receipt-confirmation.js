import { saveReceiptConfirmation } from './list-settings-client.js?v=0.89.1';

/** The same store preference in account and catalogue settings dialogs. */
export function receiptConfirmationControl(store, getData) {
  const element=document.createElement('section');
  element.className='receipt-confirmation-setting';
  element.style.cssText='margin-top:20px;padding-top:16px;border-top:1px solid var(--divider-color,#dce4e1)';
  const label=document.createElement('label');
  label.style.cssText='display:flex;align-items:center;justify-content:space-between;gap:16px;margin:0';
  const title=document.createElement('span');title.textContent='Confirmar compras automáticamente';
  const toggle=document.createElement('input');toggle.type='checkbox';toggle.role='switch';toggle.className='store-toggle';
  toggle.dataset.receiptStore=store;toggle.setAttribute('aria-label',title.textContent);toggle.style.flexShrink='0';
  label.append(title,toggle);
  const help=document.createElement('p');
  help.style.cssText='margin:10px 0 0;font-size:13px;line-height:1.5;text-align:left;color:var(--secondary-text-color,#64748b)';
  help.textContent='Desactivado: revisa el ticket antes de confirmar. Activado: repone Stock y actualiza la lista principal con las líneas reconocidas; las demás quedan pendientes. Solo para tickets compatibles.';
  const error=document.createElement('p');error.setAttribute('role','alert');error.hidden=true;
  error.style.cssText='margin:10px 0 0;font-size:13px;text-align:left;color:var(--error-color,#d32f2f)';
  element.append(label,help,error);
  let busy=false,pending,locked=false;
  const render=(disabled=false)=>{
    locked=disabled;
    const data=getData(),settings=data?.settings;
    toggle.checked=pending??(settings?.receiptConfirmation?.[store]==='automatic');
    toggle.disabled=busy||locked||data?.hass?.user?.is_admin!==true||!settings?.loaded||settings.loading;
  };
  toggle.addEventListener('change',async()=>{
    if(toggle.disabled)return;
    const session=getData()?.settings;
    pending=toggle.checked;busy=true;error.hidden=true;render(locked);
    try{await saveReceiptConfirmation(session,store,pending);}
    catch(reason){if(getData()?.settings===session){error.textContent=reason.message??'No se ha podido guardar la confirmación de compras.';error.hidden=false;}}
    finally{busy=false;pending=undefined;render(locked);}
  });
  render();
  return {element,render};
}
