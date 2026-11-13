/** One unchecked track color across independently rendered shadow roots. */
export function installToggleStyles(root) {
  if (root.querySelector('style[data-isl-dev-toggles]')) return;
  const style = document.createElement('style');
  style.dataset.islToggles = '';
  style.textContent = `
    input[type="checkbox"]:is([role="switch"],.toggle,.reader-toggle,.store-toggle):not(:checked) {
      background:var(--isl-dev-toggle-off-track,var(--switch-unchecked-track-color,#64748b));
      border-color:var(--isl-dev-toggle-off-track,var(--switch-unchecked-track-color,#64748b));
    }
  `;
  root.append(style);
}
