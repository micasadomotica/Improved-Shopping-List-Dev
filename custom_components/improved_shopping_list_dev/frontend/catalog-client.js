/** Catalog data lives in Home Assistant storage, shared by every panel session. */
const PREFIX = "improved_shopping_list_dev/catalog/";

async function catalogRequest(hass, message, timeoutMessage) {
  let timer;
  try {
    return await Promise.race([
      hass.callWS(message),
      new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(timeoutMessage)), 20000); }),
    ]);
  } finally { clearTimeout(timer); }
}

export class CatalogSession {
  constructor(getHass, onChange) {
    this.getHass = getHass;
    this.onChange = onChange;
    this._listeners = new Set();
    this.products = [];
    this.recipes = [];
    this.categories = [];
    this.recommendationCategories = [];
    this.dismissedSuggestions = [];
    this.loading = false;
    this.error = "";
    this._generation = 0;
    this._revision = 0;
  }

  subscribe(listener) {
    this._listeners.add(listener);
    return () => this._listeners.delete(listener);
  }

  _emit() {
    this.onChange();
    for (const listener of [...this._listeners]) listener();
  }

  async start() {
    this.stop();
    const generation = this._generation;
    this.loading = true;
    this.error = "";
    this._emit();
    const initialRead = this.refresh();
    try {
      const unsubscribe = await this.getHass().connection.subscribeMessage((data) => {
        if (generation !== this._generation) return;
        this._revision += 1;
        this.products = data.products ?? [];
        this.recipes = data.recipes ?? [];
        this.categories = data.categories ?? [];
        this.recommendationCategories = data.recommendation_categories ?? [];
        this.dismissedSuggestions = data.dismissed_suggestions ?? [];
        this.loading = false;
        this.error = "";
        this._emit();
      }, { type: `${PREFIX}subscribe` });
      if (generation !== this._generation) this._unsubscribeSafely(unsubscribe);
      else this._unsubscribe = unsubscribe;
    } catch (error) {
      if (generation === this._generation) {
        this.error = error.message ?? "No se ha podido conectar con el catálogo.";
        this._emit();
      }
    }
    await initialRead;
  }

  async refresh() {
    const generation = this._generation;
    const revision = ++this._revision;
    try {
      const data = await catalogRequest(this.getHass(), { type: `${PREFIX}get` }, "Home Assistant no ha respondido al cargar el catálogo. Comprueba la conexión y vuelve a abrirlo.");
      if (generation !== this._generation || revision !== this._revision) return;
      this.products = data.products ?? [];
      this.recipes = data.recipes ?? [];
      this.categories = data.categories ?? [];
      this.recommendationCategories = data.recommendation_categories ?? [];
      this.dismissedSuggestions = data.dismissed_suggestions ?? [];
      this.error = "";
    } catch (error) {
      if (generation !== this._generation || revision !== this._revision) return;
      this.error = error.message ?? "No se ha podido cargar el catálogo.";
    } finally {
      if (generation === this._generation && revision === this._revision) {
        this.loading = false;
        this._emit();
      }
    }
  }

  async save(product, replenishment) {
    const generation = this._generation;
    const result = await catalogRequest(this.getHass(), { type: `${PREFIX}save`, product, ...(replenishment ? {replenishment} : {}) },
      "No se ha podido confirmar el guardado. Puede haberse guardado en Home Assistant. Comprueba la conexión y cierra y vuelve a abrir la ficha antes de intentarlo de nuevo.");
    if (generation === this._generation) await this.refresh();
    return result.product;
  }

  async setSeasonMonths(productId, months, enabled) {
    const generation = this._generation;
    await this.getHass().callWS({type: `${PREFIX}season_months`, product_id: productId, months, enabled});
    if (generation === this._generation) await this.refresh();
  }

  async setScanQuantity(product, quantity) {
    const generation=this._generation;
    await catalogRequest(this.getHass(), {type:PREFIX+'scan_quantity',product_id:product.id,revision:product.revision,quantity},
      'No se ha podido confirmar la cantidad por escaneo. Actualiza el catálogo antes de reintentar.');
    if(generation===this._generation)await this.refresh();
  }

  async saveRecipe(recipe) {
    const generation = this._generation;
    const result = await this.getHass().callWS({type:PREFIX+"recipe/save", recipe});
    if (generation === this._generation) await this.refresh(); return result.recipe;
  }

  async deleteRecipe(recipe) {
    const generation = this._generation;
    await this.getHass().callWS({type:PREFIX+"recipe/delete", recipe_id:recipe.id, revision:recipe.revision});
    if (generation === this._generation) await this.refresh();
  }

  async setRecipeSeasonMonths(recipeId, months, enabled) {
    const generation = this._generation;
    await this.getHass().callWS({type:PREFIX+"recipe/season_months", recipe_id:recipeId, months, enabled});
    await this.refresh();
  }

  async saveRecommendationCategory(category) {
    const generation = this._generation;
    const result = await this.getHass().callWS({type:PREFIX+"recommendation_category/save", category});
    if (generation === this._generation) await this.refresh();
    return result.category;
  }

  async deleteRecommendationCategory(category) {
    const generation = this._generation;
    await this.getHass().callWS({type:PREFIX+"recommendation_category/delete", category_id:category.id, revision:category.revision});
    if (generation === this._generation) await this.refresh();
  }

  async saveCategory(category) {
    const generation = this._generation;
    const result = await this.getHass().callWS({ type: `${PREFIX}category/save`, category });
    if (generation === this._generation) await this.refresh();
    return result.category;
  }

  async deleteProduct(product) {
    const generation = this._generation;
    await this.getHass().callWS({ type: `${PREFIX}delete`, product_id: product.id, revision: product.revision });
    if (generation === this._generation) await this.refresh();
  }

  async deleteCategory(category) {
    const generation = this._generation;
    await this.getHass().callWS({ type: `${PREFIX}category/delete`, category_id: category.id, revision: category.revision });
    if (generation === this._generation) await this.refresh();
  }

  async actOnSuggestion(name, action) {
    const generation = this._generation;
    const result = await this.getHass().callWS({ type: `${PREFIX}suggestion`, name, action });
    if (generation === this._generation) await this.refresh();
    return result.product;
  }

  stop() {
    this._generation += 1;
    this._revision += 1;
    if (this._unsubscribe) this._unsubscribeSafely(this._unsubscribe);
    this._unsubscribe = undefined;
  }

  _unsubscribeSafely(unsubscribe) {
    try { Promise.resolve(unsubscribe()).catch(() => {}); } catch { /* Already disconnected. */ }
  }
}
