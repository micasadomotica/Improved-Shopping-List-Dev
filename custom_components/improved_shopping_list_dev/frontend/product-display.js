/** Lightweight product display helpers shared without importing any view. */
import { productNameKey } from "./product-names.js?v=0.88.6";
import { packageCost, weightFormat } from "./reference-formats.js?v=0.88.6";
export { productNameKey };
export const normalizedName = value => String(value ?? "").normalize("NFKC").trim().replace(/\s+/g, " ").toLocaleLowerCase("es");

export function storeFromProductUrl(value) {
  try {
    const url=new URL(value);
    if(url.protocol!=="https:"||url.username||url.password||url.port&&url.port!=="443")return "";
    return ({"www.elcorteingles.es":"elcorteingles","www.carrefour.es":"carrefour","carrefour.es":"carrefour","www.compraonline.alcampo.es":"alcampo","compraonline.alcampo.es":"alcampo",
      "tienda.masymas.com":"masymas","www.online.bmsupermercados.es":"bm","tienda.mercadona.es":"mercadona","tienda.consum.es":"consum","www.lasirena.es":"lasirena","supermercado.eroski.es":"eroski",
      "www.dia.es":"dia","dia.es":"dia","www.lidl.es":"lidl"})[url.hostname]??"";
  } catch { return ""; }
}

export function storeProductLink(store, value) {
  const program = {
    carrefour: {merchant:"25399",hosts:["www.carrefour.es","carrefour.es"],path:/^\/supermercado\/[^/]+\/R-[A-Za-z0-9-]+\/p\/?$/},
    elcorteingles: {merchant:"13075",hosts:["www.elcorteingles.es"],path:/^\/supermercado\/B[0-9]{1,20}-[^/]+\/?$/}
  }[store];
  if (!program) return value;
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || !program.hosts.includes(url.hostname) || url.username || url.password || url.port&&url.port!=="443" || !program.path.test(url.pathname)) return value;
    return `https://www.awin1.com/cread.php?awinmid=${program.merchant}&awinaffid=1535625&ued=${encodeURIComponent(value)}`;
  } catch { return value; }
}

export function cachedProductPrice(product, prices = [], favorite = "", desired = null) {
  if (!product) return null;
  const valid = offer => offer.price != null && offer.price !== "" && Number.isFinite(Number(offer.price)) &&
    Number(offer.price) >= 0 && /^[A-Z]{3}$/.test(offer.currency ?? "");
  const offers = prices.filter(offer => offer.catalog_product_id === product.id && valid(offer));
  const preferred=product.preferred_reference&&(product.commercial_references??[]).find(ref=>ref.id===product.preferred_reference&&ref.active!==false);
  if(preferred){
    const choices=offers.filter(offer=>matchesReference(offer,preferred)).sort((a,b)=>(b.checked_at??0)-(a.checked_at??0));
    return choices[0]??null;
  }
  const quantity=desired?.quantity??product.unit_quantity??1,unit=desired?.unit??product.purchase_unit??'uds';
  const cost=offer=>packageCost(offer,quantity,unit)?.price??Infinity;
  offers.sort((a,b) => Number(b.store === favorite) - Number(a.store === favorite) || cost(a)-cost(b) || (b.checked_at ?? 0) - (a.checked_at ?? 0));
  return offers[0] ?? (valid(product) ? {price:product.price,currency:product.currency} : null);
}

const matchesReference=(offer,ref)=>offer.store===ref.store&&(ref.id&&offer.commercial_reference_id===ref.id||ref.reference_id&&offer.reference_id===ref.reference_id||!ref.reference_id&&ref.ean&&offer.ean===ref.ean);

export function productPriceLabel(offer,product) {
  const ref=(product?.commercial_references??[]).find(ref=>ref.active!==false&&matchesReference(offer,ref));
  const piece=product?.preferred_reference&&ref?.id===product.preferred_reference&&weightFormat(ref)&&ref.last_price?.estimated;
  const price=piece?ref.last_price:offer;
  const unit=piece?'ud.':ref?(weightFormat(ref)?'kg':'envase'):'';
  const estimated=piece||price.estimated;
  return (estimated?'≈ ':'')+new Intl.NumberFormat('es-ES',{style:'currency',currency:price.currency,minimumFractionDigits:2,maximumFractionDigits:2}).format(Number(price.price))+(unit?' / '+unit:'');
}

export function preferredPurchaseCost(product,offer,desired) {
  const ref=product?.preferred_reference&&(product.commercial_references??[]).find(ref=>ref.id===product.preferred_reference&&ref.active!==false&&matchesReference(offer,ref));
  if(!ref||desired?.unit!=='uds'||!(desired.quantity>0))return null;
  // Known package contents still use the normal units-to-packages conversion.
  if(!weightFormat(ref)&&ref.content_confirmed&&ref.content_quantity>0)return null;
  const price=weightFormat(ref)?(ref.last_price?.estimated?ref.last_price:null):offer;
  if(!price)return null;
  const packages=Math.ceil(desired.quantity-1e-9);
  return {price:Math.round(Number(price.price)*packages*100)/100,packages,estimated:Boolean(price.estimated),price_basis:weightFormat(ref)?'ud.':'envase'};
}

export function imageSource(value) {
  if (!value) return "";
  try {
    const url = new URL(value, location.origin);
    return ["http:", "https:"].includes(url.protocol) ? url.href : "";
  } catch { return ""; }
}

