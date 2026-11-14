# Probar Improved Shopping List Dev

Repositorio: https://github.com/micasadomotica/Improved-Shopping-List-Dev

Versión actual: **0.89.3**, basada en la 0.88.0 de TitoTB, con las correcciones de URL y Mercadona.

## Convivencia con el original

| Recurso | Original | Dev |
|---|---|---|
| Dominio | `improved_shopping_list` | `improved_shopping_list_dev` |
| Panel | `/improved-shopping-list` | `/improved-shopping-list-dev` |
| Archivos | `custom_components/improved_shopping_list` | `custom_components/improved_shopping_list_dev` |
| Datos internos | `improved_shopping_list.*` | `improved_shopping_list_dev.*` |
| Componentes del navegador | `isl-*` | `isl-dev-*` |

Dev tiene configuración, catálogo, compras, Stock, iconos y preferencias de navegador propios. No importa automáticamente los datos, las cuentas ni los ajustes del original. La instalación original permanece intacta.

**Las entidades `todo` pertenecen a sus proveedores, no a Dev.** Si eliges la misma entidad en ambas integraciones, ambas podrán modificar sus artículos. Crea una lista local de tareas para las pruebas y selecciónala al configurar Dev. Solo esa lista se añade inicialmente; las demás deben añadirse expresamente desde Ajustes. Las cuentas externas y las notificaciones tampoco se duplican automáticamente.

## Instalación con Marketplace

1. Mantén instalado el original. No hace falta desinstalarlo ni sustituir archivos.
2. En **Ajustes → Marketplace → Añadir desde enlace**, añade `https://github.com/micasadomotica/Improved-Shopping-List-Dev`. Conecta GitHub si se solicita. El tipo es **Integración**.
3. Abre **Improved Shopping List Dev** e instala **0.89.3**. Es una release normal de este repositorio de desarrollo: no requiere habilitar versiones preliminares.
4. Sigue el aviso de reinicio si aparece. En **Ajustes → Dispositivos y servicios → Añadir integración**, busca **Improved Shopping List Dev** si no se abre el asistente automáticamente.
5. Selecciona la lista de pruebas. Abre el panel **Improved Shopping List Dev** y comprueba su versión.

Estas instrucciones corresponden a Marketplace de Home Assistant 2026.11 o posterior. El código conserva su compatibilidad mínima con Core 2026.10. El archivo técnico `hacs.json` sigue siendo necesario en Marketplace.

## Zona de Mercadona

En **Dev → Ajustes → Tiendas → Catálogos → Mercadona (engranaje) → Código postal de entrega**, indica el mismo código postal de entrega que en su web y pulsa **Guardar zona**. Se consulta el almacén correspondiente sin iniciar sesión y se conserva la zona anterior si Mercadona no puede confirmarla. Dejar el campo vacío restablece la zona de referencia predeterminada.

El código postal se guarda en la configuración local de Home Assistant y se envía a Mercadona para resolver la zona; no se publica en GitHub. No activa la tienda ni modifica los ajustes del original. Los precios y pesos aproximados pueden variar entre zonas.

Después de guardar la zona, abre las alternativas existentes, pulsa **Recuperar datos** en cada URL y guarda el producto. Los precios ya guardados no se cambian automáticamente por abrir este ajuste.

## Favoritos y alternativa preferida

En **Ajustes → Tiendas**, el corazón de una cuenta o catálogo elige la tienda favorita para la selección automática de precios. También está disponible en Mercadona. Marcar una tienda no activa consultas ni conecta cuentas; se usan los precios ya guardados.

En **Catálogo → Editar producto → Alternativas → Alternativa preferida**, selecciona una referencia concreta o **Automática**. Guarda el producto. La elección concreta tiene prioridad sobre la tienda favorita y se conserva al recargar. Si desvinculas la alternativa, vuelve a Automática. Si la alternativa elegida no tiene precio guardado, no se muestra un precio de otra referencia.

El precio del catálogo indica su formato: por envase, por kilo o por unidad aproximada cuando eliges una pieza con esa estimación. Las cantidades con contenido conocido siguen convirtiéndose a envases; una pieza estimada nunca se interpreta como un kilo. La vista de comparación por tiendas continúa mostrando las opciones de cada tienda.

El desplegable de alternativa preferida también muestra el precio y su formato. Si editas un precio, su etiqueta se actualiza antes de guardar. Las alternativas sin precio se identifican con «Sin precio».

Las fichas de Mercadona con referencias como `83202.1` (barra de pan) y `83203.1` (tres barras) conservan el identificador completo, separado del EAN. Si se pierde la respuesta al guardar un producto, el editor avisa tras 20 segundos: el cambio puede haberse guardado. Comprueba la conexión y vuelve a abrir la ficha antes de repetirlo.

## Pruebas de las correcciones

- Crea un aguacate de prueba en el catálogo de Dev.
- Pega `https://tienda.mercadona.es/product/3830/aguacate-pieza`, guarda sin recuperar datos y vuelve a abrir: debe conservarse el enlace. Debe ocurrir lo mismo si falla la consulta.
- Recupera esa ficha: la alternativa de Mercadona debe usar la referencia `3830` y dejar vacío el EAN compartido.
- Añade `https://tienda.mercadona.es/product/3858/aguacates-bandeja`: debe ser otra alternativa, con referencia `3858`.
- La pieza debe mostrar Al peso / kg, el precio estimado por unidad y la tarifa por kilo. La bandeja debe mostrar Por envase y su precio estimado por pack. No se inventan gramos exactos ni piezas de la bandeja. Recuperar de nuevo la ficha corrige una bandeja guardada antes como Al peso.
- Desvincula una alternativa de Mercadona, guarda y vuelve a añadir la misma URL: debe recuperarse sin duplicarla ni impedir el guardado.
- Una alternativa existente con EAN, por ejemplo de Lidl, debe conservar sus datos al recuperar una URL de Mercadona: el resultado crea o reutiliza una alternativa separada.
- Abre el panel original y el de Dev en la misma sesión de navegador. Comprueba que conservan sus catálogos, ajustes y listas seleccionadas.

## Datos y retirada de Dev

No se ha implementado una copia completa de datos entre el original y Dev. Exportar el catálogo no equivale a una copia de toda la configuración, compras y Stock. Para comenzar las pruebas se usan datos de prueba. Una futura copia debe ser explícita, sin compartir almacenamiento entre ambas integraciones.

Para dejar de probar, puedes deshabilitar la integración **Dev**. Para eliminarla, comprueba que estás en **Improved Shopping List Dev** antes de borrar su configuración y desinstalar su repositorio. Esto elimina las entidades de Dev; las listas externas `todo` son de su proveedor y pueden seguir existiendo. Si quieres conservar los datos de pruebas antes de retirar Dev, crea una copia de seguridad de Home Assistant.

## Cambios para el original

El fork `micasadomotica/Improved-Shopping-List` conserva la rama `codex/product-url-mercadona-fixes` para proponer las correcciones a TitoTB. Los cambios de nombre y aislamiento pertenecen únicamente a este repositorio Dev. Se conserva el historial del proyecto y su licencia MIT.

Solo se incluye el workflow **Validate**. Las publicaciones de versiones se hacen manualmente; no se ejecutan los workflows heredados de publicación ni de diccionarios. Los diccionarios públicos consultados siguen siendo los del proyecto original.

Fuente: [documentación oficial de Marketplace](https://www.home-assistant.io/integrations/marketplace).
