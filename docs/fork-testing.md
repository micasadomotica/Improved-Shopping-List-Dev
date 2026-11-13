# Probar Improved Shopping List Dev

Repositorio: https://github.com/micasadomotica/Improved-Shopping-List-Dev

Versión actual: **0.88.2**, basada en la 0.88.0 de TitoTB, con las correcciones de URL y Mercadona.

## Convivencia con el original

| Recurso | Original | Dev |
|---|---|---|
| Dominio | `improved_shopping_list` | `improved_shopping_list_dev` |
| Panel | `/improved-shopping-list` | `/improved-shopping-list-dev` |
| Archivos | `custom_components/improved_shopping_list` | `custom_components/improved_shopping_list_dev` |
| Datos internos | `improved_shopping_list.*` | `improved_shopping_list_dev.*` |
| Componentes del navegador | `isl-*` | `isl-dev-*` |

Dev tiene configuración, catálogo, compras, Stock, iconos y preferencias de navegador propios. No importa automáticamente los datos, las cuentas ni los ajustes del original. La instalación original permanece intacta.

**Las entidades `todo` pertenecen a sus proveedores, no a Dev.** Si eliges la misma entidad en ambas integraciones, ambas podrán modificar sus artículos. Puedes seleccionar tu lista habitual y compartir sus artículos, o crear una lista local para aislarlos también. Solo esa lista se añade inicialmente; las demás deben añadirse expresamente desde Ajustes. Las cuentas externas y las notificaciones tampoco se duplican automáticamente.

## Instalación con Marketplace

1. Mantén instalado el original. No hace falta desinstalarlo ni sustituir archivos.
2. En **Ajustes → Marketplace → Añadir desde enlace**, añade `https://github.com/micasadomotica/Improved-Shopping-List-Dev`. Conecta GitHub si se solicita. El tipo es **Integración**.
3. Abre **Improved Shopping List Dev** e instala **0.88.2**. Es una release normal de este repositorio de desarrollo: no requiere habilitar versiones preliminares.
4. Sigue el aviso de reinicio si aparece. En **Ajustes → Dispositivos y servicios → Añadir integración**, busca **Improved Shopping List Dev** si no se abre el asistente automáticamente.
5. Selecciona la lista que quieres usar en Dev. Si quieres tus datos actuales, marca **Copiar los datos del original a Dev**. El original debe estar configurado y activo. Si eliges una lista del original, marca también **Permitir compartir la lista con el original**. Los artículos se comparten, mientras que el catálogo y los ajustes tienen copias propias.
6. Abre el panel **Improved Shopping List Dev** y comprueba su versión y catálogo.

Estas instrucciones corresponden a Marketplace de Home Assistant 2026.11 o posterior. El código conserva su compatibilidad mínima con Core 2026.10. El archivo técnico `hacs.json` sigue siendo necesario en Marketplace.

## Pruebas de las correcciones

- Crea un aguacate de prueba en el catálogo de Dev.
- Pega `https://tienda.mercadona.es/product/3830/aguacate-pieza`, guarda sin recuperar datos y vuelve a abrir: debe conservarse el enlace. Debe ocurrir lo mismo si falla la consulta.
- Recupera esa ficha: la alternativa de Mercadona debe usar la referencia `3830` y dejar vacío el EAN compartido.
- Añade `https://tienda.mercadona.es/product/3858/aguacates-bandeja`: debe ser otra alternativa, con referencia `3858`.
- En la consulta del 13 de noviembre de 2026 ambas fichas publicaban peso aproximado. Se tratan como venta al peso, con precio por kilogramo; no se inventan gramos exactos ni piezas de la bandeja.
- Una alternativa existente con EAN, por ejemplo de Lidl, debe conservar sus datos al recuperar una URL de Mercadona: el resultado crea o reutiliza una alternativa separada.
- Abre el panel original y el de Dev en la misma sesión de navegador. Comprueba que conservan sus catálogos, ajustes y listas seleccionadas.

## Datos y retirada de Dev

Desde 0.88.2, la configuración inicial permite copiar una vez el catálogo (productos, alternativas, categorías, recetas y grupos), Stock y compras guardadas, historial de precios, datos nutricionales ya consultados e iconos personalizados. También copia la apariencia y las preferencias de tiendas. Los precios guardados en las alternativas se conservan.

La copia mantiene los identificadores internos para conservar las relaciones entre productos, Stock y compras, pero los guarda exclusivamente bajo el dominio de Dev. Los iconos locales se copian y sus URL se cambian a Dev. No hay sincronización posterior: editar un producto en Dev no cambia el original, y los cambios posteriores del original tampoco llegan a Dev.

No se copian los artículos de las listas todo, sus cantidades/UID, sesiones o credenciales de cuentas, lectores físicos, pasarelas, notificaciones, detección de compras ni tareas en curso. Dev selecciona únicamente la lista elegida. Si permites compartirla con el original, no se duplican sus artículos: ambos paneles modifican la misma lista. La actualización programada de precios y la recurrencia quedan apagadas; Stock conserva las cantidades del momento de la copia en modo manual, sin reposición automática ni consumo por sensores/comidas. Puedes configurar estas funciones después en Dev de manera explícita.

La copia requiere un Dev vacío y se ofrece al crear su configuración, no como una operación que sobrescriba datos existentes. Si una escritura falla, Dev no inicia sus tareas y reintenta el mismo conjunto de datos guardado temporalmente, sin recapturar ni modificar el original. Una vez completada, los reinicios no repiten la copia.

## Icono de la integración

Dev incluye `brand/icon.png` para Home Assistant 2026.3 o posterior. Se utiliza tras instalar la integración; la imagen anterior a la instalación en Marketplace depende del catálogo público de marcas, donde un repositorio añadido manualmente puede no figurar. El catálogo personal y las fotos de productos son independientes de este icono. [Documentación oficial](https://contributors.home-assistant.io/marketplace/guides/integrations/#brand-images).

Para dejar de probar, puedes deshabilitar la integración **Dev**. Para eliminarla, comprueba que estás en **Improved Shopping List Dev** antes de borrar su configuración y desinstalar su repositorio. Esto elimina las entidades de Dev; las listas externas `todo` son de su proveedor y pueden seguir existiendo. Si quieres conservar los datos de pruebas antes de retirar Dev, crea una copia de seguridad de Home Assistant.

## Cambios para el original

El fork `micasadomotica/Improved-Shopping-List` conserva la rama `codex/product-url-mercadona-fixes` para proponer las correcciones a TitoTB. Los cambios de nombre y aislamiento pertenecen únicamente a este repositorio Dev. Se conserva el historial del proyecto y su licencia MIT.

Solo se incluye el workflow **Validate**. Las publicaciones de versiones se hacen manualmente; no se ejecutan los workflows heredados de publicación ni de diccionarios. Los diccionarios públicos consultados siguen siendo los del proyecto original.

Fuente: [documentación oficial de Marketplace](https://www.home-assistant.io/integrations/marketplace).
