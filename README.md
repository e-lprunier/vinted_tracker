# 🏷️ Vinted Tracker

Détecteur de **bonnes affaires sur Vinted** : il scanne des recherches ciblées (sneakers, Ralph Lauren,
Carhartt, bijoux, objets…), **écarte les faux prix et les arnaques** en analysant le texte des annonces,
puis calcule la **marge nette réelle** (frais Vinted + livraison + emballage + cotisations) avant de classer
les meilleures opportunités dans un dashboard Streamlit.

> Partie 1 (ce repo) : détection + dashboard. Partie 2 (à venir) : agents de négociation, voir [Roadmap](#partie-2--agents-de-négociation).

---

## Ce que fait l'outil

| Étape | Détail |
|---|---|
| **Collecte** | API interne Vinted (`curl_cffi`, depuis ton PC) ou Apify (depuis le cloud). Watchlist dans `config.yaml`. |
| **Faux prix** | « 1 € affiché, *prix réel 90 €* dans la description », « je la laisse à 85 € ferme », « prix en description », prix symbolique ≤ 2 €… → **exclu**. « Acheté 120 € en boutique » est reconnu comme **prix neuf** (utile), pas comme faux prix. |
| **Arnaques** | WhatsApp, PayPal, virement, téléphone, e-mail, lien externe → **exclu**. |
| **Contrefaçons** | replica, 1:1, copie, « pas authentique » → **exclu** (« pas de fake » n'est pas compté). « Style / façon Nike » et prix trop bas sur marque très contrefaite → **à vérifier**. |
| **Annonces non valables** | boîte vide, photo, pour pièces, réservé, « je recherche », échange uniquement → **exclu**. Lots, tailles enfant, hors-sujet → **non comparables**. |
| **Prix de marché** | médiane des annonces *identiques ou très proches* : même **modèle** (déduit du titre), même **couleur**, même **taille** (tailles voisines regroupées). Si moins de 5 annonces, on élargit automatiquement (modèle + couleur → modèle → couleur → recherche entière). Prix ramenés à « Très bon état » : neuf étiqueté ×1,25 · neuf sans étiquette ×1,05 · TBE ×1 · bon ×0,75 · satisfaisant ×0,5. |
| **Demande** | favoris de l'annonce comparés à ceux des annonces similaires, et favoris par heure depuis l'apparition de l'annonce (Vinted ne donne plus la date de mise en ligne : elle est mesurée scan après scan, donc fiable seulement pour les annonces apparues après le premier scan). 🔥 = forte demande. |
| **Marge** | `revente estimée × (1 − cotisations) − emballage − (prix + 0,70 € + 5 % + port)` + **prix d'achat max** pour garder ta marge cible. |
| **Vendeur** | exclu si moins de 3 avis ou note ≤ 3,5★. |
| **Score /100** | décote vs marché, marge, ROI, décote vs neuf, demande, moins le risque. |

### Frais utilisés (vérifiés en novembre 2026, modifiables dans `config.yaml`)
- **Acheteur** : « Frais Vinted » (ex-Protection acheteurs) = **0,70 € + 5 %** du prix de l'article.
- **Vendeur particulier** : **0 % de commission** — tu touches le prix affiché.
- **Livraison point relais** (paramétrable) : petit 3,30 € · moyen 5,50 € · grand 7,50 €. Calibre ces valeurs avec tes paniers réels.
- **Cotisations** micro-entrepreneur achat-revente : 12,3 % (toggle dans le dashboard).

---

## Démarrage rapide (local)

```bash
git clone https://github.com/e-lprunier/vinted_tracker && cd vinted_tracker
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python -m vinted_tracker scan                       # un scan des annonces Vinted réelles
python -m vinted_tracker watch --every 30 --push    # rescan toutes les 30 min + push -> app en ligne à jour
streamlit run streamlit_app.py                      # ouvre le dashboard en local
```

Options utiles : `--search "Nike Dunk Low"` (répétable), `--source vinted|apify|auto` (auto = Vinted direct, Apify si bloqué), `--top 30`.
Un scan complet (16 recherches × 96 annonces les plus récentes + descriptions des meilleures) prend ~5-8 min, délais anti-blocage compris.
Tests : `python -m pytest -q`.

## Déploiement Streamlit Cloud (lien public « Vinted Tracker »)

1. Va sur **https://share.streamlit.io** → connecte ton compte GitHub.
2. **Create app** → *Deploy a public app from GitHub* :
   - Repository : `e-lprunier/vinted_tracker` · Branch : `main` · Main file : `streamlit_app.py`
   - App URL : choisis `vinted-tracker` → `https://vinted-tracker.streamlit.app` (si dispo).
3. *Advanced settings* → **Secrets** (facultatif) :
   ```toml
   APIFY_TOKEN = "apify_api_xxx"        # pour lancer des scans depuis l'app
   ANTHROPIC_API_KEY = "sk-ant-xxx"     # si llm.enabled: true
   ```
4. Deploy. L'app est **publique par défaut** : passe-la en privée dans *Settings → Sharing* si tu ne veux pas exposer tes trouvailles.

### Comment les données arrivent dans l'app en ligne
Les IP de serveurs (Streamlit Cloud, GitHub Actions) sont **généralement bloquées par l'anti-bot de Vinted**. Deux options :

- **Simple** : tu scannes depuis ton PC (`--source vinted`), puis `git add data/latest_scan.parquet && git commit -m scan && git push` → l'app se met à jour.
- **Automatique** : le workflow `.github/workflows/scan.yml` lance un scan **via Apify** toutes les 3 h et commit le snapshot. Ajoute le secret `APIFY_TOKEN` dans *Settings → Secrets → Actions* et la variable `SCAN_ENABLED=true` (ou lance-le à la main via *Actions → Scan Vinted → Run workflow*). Apify est payant à l'usage ; vérifie le format d'entrée de l'actor choisi (`config.yaml → apify`).

L'app n'affiche **que des annonces réelles** : au chargement, si les données ont plus de 30 min (`config.yaml → app`), elle relance un scan en arrière-plan (Vinted direct, puis Apify si un `APIFY_TOKEN` est configuré). Si aucun des deux ne passe depuis le serveur, elle l'explique et attend le prochain snapshot poussé depuis ton PC. Le badge 🆕 marque les annonces apparues depuis le scan précédent.

---

## Structure

```
streamlit_app.py            dashboard temps réel (affaires, marché, écartées, calculateur, méthode)
config.yaml                 frais, seuils, watchlist de recherches
data/reference_prices.csv   prix neufs indicatifs (à vérifier / compléter)
vinted_tracker/
  sources/vinted_api.py     client API Vinted (curl_cffi, cookies de session, délais aléatoires)
  sources/apify_source.py   alternative cloud via Apify
  background.py             scan en arrière-plan dans l'app (auto-actualisation)
  text_analysis.py          ⭐ détection faux prix / arnaques / contrefaçons
  comparables.py            comparables fins : modèle / couleur / taille, avec repli
  pricing.py                frais, coût total, marge, prix d'achat max
  scoring.py                comparables, risque, score
  llm_check.py              second avis Claude (optionnel, sur les cas « à vérifier »)
  storage.py                SQLite (historique) + snapshot Parquet (lu par l'app)
  pipeline.py · cli.py
tests/                      28 tests (faux prix, arnaques, faux positifs, marge) — seules les
                            annonces fictives des tests vivent ici, jamais dans l'app
```

## Limites à connaître
- Les prix de marché sont des prix **demandés**, pas vendus (Vinted masque de plus en plus les ventes) → décote de 10 % appliquée, réglable.
- L'API Vinted n'est pas publique : les endpoints et l'anti-bot évoluent, le client peut casser.
- Une annonce « ok » n'est **pas** une garantie d'authenticité : regarde photos + profil vendeur.
- Le fichier de prix neufs est **indicatif**.

## ⚖️ Cadre Vinted & fiscal (à lire avant de scaler)
- **Automatisation** : les conditions de Vinted ne prévoient pas les outils tiers qui agissent sur la plateforme ; un usage intensif peut mener à une suspension de compte. Le scan en lecture avec des délais raisonnables est le moins risqué ; les actions automatiques (messages, offres, achats) le sont beaucoup plus.
- **Achat pour revendre** : c'est une activité commerciale (micro-BIC achat-revente), distincte du vide-dressing. Vinted demande aux vendeurs professionnels d'utiliser **Vinted Pro**, et transmet aux impôts les comptes qui dépassent les seuils DAC7 (30 ventes ou 2 000 € par an).

---

## Partie 2 — agents de négociation

Le dashboard calcule déjà, pour chaque annonce, le **`max_buy_price`** : le prix au-delà duquel ta marge cible disparaît. C'est la limite dure des futurs agents.

**Agent acheteur** (pour chaque bonne affaire) :
1. Offre d'ouverture = `min(prix × 0,75, max_buy_price × 0,85)`, arrondie « humainement ».
2. Concessions décroissantes (ex. +40 %, +25 %, +15 % de l'écart restant), jamais au-delà de `max_buy_price`.
3. Messages courts et polis rédigés par Claude, avec garde-fous : pas de mensonge, pas de sortie de Vinted, pas de pression.
4. Arrêt automatique : refus ferme, 3 tours, ou annonce vendue.

**Agent vendeur** (pour tes reventes) :
- Prix plancher = coût total + marge mini ; acceptation auto des offres ≥ plancher, contre-offre sinon.
- Réponses aux questions à partir des infos de l'annonce (taille, état, envoi).

**Recommandation** : commencer en mode **« copilote »** — l'agent prépare l'offre et le message, tu valides d'un clic. Ça garde 90 % du gain de temps, tout en limitant le risque de suspension et les erreurs coûteuses.
