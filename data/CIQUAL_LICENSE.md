# CIQUAL data — attribution and license

The file `ciqual_subset.json` in this directory is a small, curated
subset of the **ANSES Ciqual 2020 food composition table**:

> ANSES — Agence nationale de sécurité sanitaire de l'alimentation,
> de l'environnement et du travail.
> *Table de composition nutritionnelle des aliments Ciqual 2020.*
> https://ciqual.anses.fr/

Values are per 100 g of the edible portion. English names and aliases
have been added to match the items in `food_items.json`; the underlying
numeric values (kcal, protein, fat, saturated fat, carbohydrates, sugar,
fibre, sodium) are taken unchanged from the corresponding CIQUAL records.

Per ANSES policy, the Ciqual data may be re-used for research and
information purposes with attribution. See
<https://ciqual.anses.fr/mentions-legales> for the authoritative terms.

This repository is a technical evaluation exercise; the subset is
shipped only to make the verification agent runnable offline. For any
downstream or production use, pull the full dataset directly from the
ANSES portal.
