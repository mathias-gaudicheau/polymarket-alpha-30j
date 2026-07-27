-- Schema de l'experience. Applique de facon idempotente par initialiser_bdd.py.
--
-- Contrainte structurante : la base gratuite plafonne a 0,5 Go. La table des
-- instantanes est la seule qui grossit vraiment, elle est donc compactee au
-- maximum : les prix y sont stockes en milliemes sur un entier court (le pas
-- de cotation de Polymarket vaut 0,001, la conversion est donc sans perte) et
-- les dates en secondes depuis l'epoque plutot qu'en horodatage complet.
--
-- Les tables de decision (signal, execution, position) ne sont jamais purgees :
-- ce sont elles qui portent la preuve, et elles sont minuscules.

-- ---------------------------------------------------------------- univers --

CREATE TABLE IF NOT EXISTS marche (
    id              INTEGER PRIMARY KEY,
    condition_id    TEXT,
    question        TEXT,
    slug            TEXT,
    jeton_oui       TEXT,
    jeton_non       TEXT,
    neg_risk        BOOLEAN DEFAULT FALSE,
    neg_risk_id     TEXT,
    groupe_titre    TEXT,
    groupe_seuil    DOUBLE PRECISION,
    evenement_id    TEXT,
    evenement_slug  TEXT,
    evenement_titre TEXT,
    etiquettes      TEXT[],
    categorie       TEXT,
    taux_frais      REAL,
    source_taux     TEXT,
    taille_min      REAL,
    pas_de_prix     REAL,
    ecart_max_prime REAL,
    fin             TIMESTAMPTZ,
    debut           TIMESTAMPTZ,
    cree            TIMESTAMPTZ,
    vu_le           TIMESTAMPTZ DEFAULT now(),
    clos            BOOLEAN DEFAULT FALSE,
    surveille       BOOLEAN DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_marche_evenement ON marche (evenement_id);
CREATE INDEX IF NOT EXISTS idx_marche_negrisk   ON marche (neg_risk_id) WHERE neg_risk;
CREATE INDEX IF NOT EXISTS idx_marche_fin       ON marche (fin) WHERE NOT clos;
CREATE INDEX IF NOT EXISTS idx_marche_surveille ON marche (surveille) WHERE surveille;

-- ------------------------------------------------------------ instantanes --
-- Prix en milliemes (0 a 1000). Ecriture uniquement sur changement notable.

CREATE TABLE IF NOT EXISTS instantane (
    marche_id   INTEGER NOT NULL,
    t           INTEGER NOT NULL,
    achat       SMALLINT,
    vente       SMALLINT,
    dernier     SMALLINT,
    liquidite   INTEGER,
    volume24    INTEGER,
    PRIMARY KEY (marche_id, t)
);

CREATE INDEX IF NOT EXISTS idx_instantane_t ON instantane (t);

-- Agregat horaire, alimente par la compaction quand le brut est purge.
CREATE TABLE IF NOT EXISTS instantane_horaire (
    marche_id   INTEGER NOT NULL,
    heure       INTEGER NOT NULL,
    achat_moy   SMALLINT,
    vente_moy   SMALLINT,
    achat_min   SMALLINT,
    vente_max   SMALLINT,
    nb_points   SMALLINT,
    PRIMARY KEY (marche_id, heure)
);

-- Carnets complets : uniquement pour les marches ou un signal se declenche.
CREATE TABLE IF NOT EXISTS carnet (
    marche_id   INTEGER NOT NULL,
    jeton       TEXT NOT NULL,
    t           INTEGER NOT NULL,
    achats      JSONB,
    ventes      JSONB,
    PRIMARY KEY (marche_id, jeton, t)
);

-- Bande de transactions, pour modeliser un remplissage a cours limite.
CREATE TABLE IF NOT EXISTS transaction_marche (
    id          BIGSERIAL PRIMARY KEY,
    marche_id   INTEGER,
    condition_id TEXT,
    t           INTEGER NOT NULL,
    prix        SMALLINT,
    taille      REAL,
    sens        TEXT,
    issue       SMALLINT,
    portefeuille TEXT
);

CREATE INDEX IF NOT EXISTS idx_transaction_marche_t ON transaction_marche (marche_id, t);
CREATE INDEX IF NOT EXISTS idx_transaction_portef   ON transaction_marche (portefeuille, t);

-- ------------------------------------------------------ references externes --

CREATE TABLE IF NOT EXISTS reference_externe (
    id          BIGSERIAL PRIMARY KEY,
    source      TEXT NOT NULL,
    marche_id   INTEGER,
    cle_externe TEXT,
    t           INTEGER NOT NULL,
    proba       DOUBLE PRECISION,
    prix_polymarket DOUBLE PRECISION,
    ecart       DOUBLE PRECISION,
    detail      JSONB
);

CREATE INDEX IF NOT EXISTS idx_reference_marche ON reference_externe (marche_id, t);
CREATE INDEX IF NOT EXISTS idx_reference_source ON reference_externe (source, t);

-- Poids de confiance appris pour chaque source, reevalue sur les denouements.
CREATE TABLE IF NOT EXISTS confiance_source (
    source          TEXT PRIMARY KEY,
    nb_denouements  INTEGER DEFAULT 0,
    brier_source    DOUBLE PRECISION,
    brier_marche    DOUBLE PRECISION,
    poids           DOUBLE PRECISION DEFAULT 0.0,
    maj_le          TIMESTAMPTZ DEFAULT now()
);

-- ------------------------------------------------------------ strategies --
-- Le registre reflete PROTOCOLE.md ; en cas de divergence, git fait foi.

CREATE TABLE IF NOT EXISTS strategie (
    code            TEXT PRIMARY KEY,
    libelle         TEXT,
    couche          SMALLINT,
    etat            TEXT NOT NULL DEFAULT 'candidat',
    declaree_le     TIMESTAMPTZ,
    commit_sha      TEXT,
    activee_le      TIMESTAMPTZ,
    suspendue_le    TIMESTAMPTZ,
    motif_suspension TEXT,
    capital_alloue  DOUBLE PRECISION DEFAULT 0,
    regles          JSONB,
    CONSTRAINT etat_connu CHECK (etat IN ('candidat','declare','actif','suspendu'))
);

-- ---------------------------------------------------------- decisions ------

CREATE TABLE IF NOT EXISTS signal (
    id              BIGSERIAL PRIMARY KEY,
    strategie       TEXT NOT NULL,
    t_signal        TIMESTAMPTZ NOT NULL,
    marche_pivot    INTEGER,
    avantage        DOUBLE PRECISION,
    avantage_net    DOUBLE PRECISION,
    proba_estimee   DOUBLE PRECISION,
    prix_reference  DOUBLE PRECISION,
    gain_certain    DOUBLE PRECISION,
    tout_ou_rien    BOOLEAN DEFAULT FALSE,
    explication     TEXT,
    jambes          JSONB NOT NULL,
    temoin          BOOLEAN DEFAULT FALSE,
    signal_jumeau   BIGINT,
    etat            TEXT DEFAULT 'emis'
);

CREATE INDEX IF NOT EXISTS idx_signal_strategie ON signal (strategie, t_signal);
CREATE INDEX IF NOT EXISTS idx_signal_etat      ON signal (etat) WHERE etat = 'emis';

CREATE TABLE IF NOT EXISTS execution (
    signal_id       BIGINT PRIMARY KEY REFERENCES signal(id),
    t_execution     TIMESTAMPTZ NOT NULL,
    realisee        BOOLEAN NOT NULL,
    motif_echec     TEXT,
    parts           DOUBLE PRECISION,
    prix_moyen      DOUBLE PRECISION,
    frais           DOUBLE PRECISION,
    montant_net     DOUBLE PRECISION,
    borne_profondeur BOOLEAN,
    detail_jambes   JSONB
);

CREATE TABLE IF NOT EXISTS position (
    id              BIGSERIAL PRIMARY KEY,
    signal_id       BIGINT REFERENCES signal(id),
    strategie       TEXT NOT NULL,
    marche_id       INTEGER NOT NULL,
    jeton           TEXT NOT NULL,
    parts           DOUBLE PRECISION NOT NULL,
    prix_entree     DOUBLE PRECISION NOT NULL,
    frais_entree    DOUBLE PRECISION DEFAULT 0,
    ouverte_le      TIMESTAMPTZ NOT NULL,
    fermee_le       TIMESTAMPTZ,
    prix_sortie     DOUBLE PRECISION,
    resultat        DOUBLE PRECISION,
    temoin          BOOLEAN DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_position_ouverte ON position (marche_id) WHERE fermee_le IS NULL;
CREATE INDEX IF NOT EXISTS idx_position_strat   ON position (strategie, ouverte_le);

CREATE TABLE IF NOT EXISTS denouement (
    marche_id   INTEGER PRIMARY KEY,
    t           TIMESTAMPTZ NOT NULL,
    issue_oui   BOOLEAN,
    prix_final  DOUBLE PRECISION,
    source      TEXT
);

-- ----------------------------------------------------------- portefeuille --

CREATE TABLE IF NOT EXISTS capital (
    jour            DATE PRIMARY KEY,
    total           DOUBLE PRECISION NOT NULL,
    disponible      DOUBLE PRECISION NOT NULL,
    engage          DOUBLE PRECISION NOT NULL,
    reserve         DOUBLE PRECISION NOT NULL,
    resultat_jour   DOUBLE PRECISION DEFAULT 0,
    resultat_cumule DOUBLE PRECISION DEFAULT 0,
    temoin_cumule   DOUBLE PRECISION DEFAULT 0,
    nb_positions    INTEGER DEFAULT 0,
    detail_strategies JSONB
);

-- --------------------------------------------------- argent intelligent ----

CREATE TABLE IF NOT EXISTS portefeuille_suivi (
    adresse         TEXT PRIMARY KEY,
    pseudonyme      TEXT,
    gain_declare    DOUBLE PRECISION,
    volume_declare  DOUBLE PRECISION,
    nb_observations INTEGER DEFAULT 0,
    nb_denouements  INTEGER DEFAULT 0,
    taux_reussite   DOUBLE PRECISION,
    brier           DOUBLE PRECISION,
    classe          TEXT,
    maj_le          TIMESTAMPTZ DEFAULT now()
);

-- --------------------------------------------------------------- sante -----

CREATE TABLE IF NOT EXISTS cycle (
    id              BIGSERIAL PRIMARY KEY,
    t               TIMESTAMPTZ NOT NULL DEFAULT now(),
    genre           TEXT NOT NULL,
    duree_ms        INTEGER,
    marches_vus     INTEGER,
    instantanes_ecrits INTEGER,
    signaux_emis    INTEGER,
    erreurs         INTEGER DEFAULT 0,
    detail          JSONB
);

CREATE INDEX IF NOT EXISTS idx_cycle_t ON cycle (t DESC);

CREATE TABLE IF NOT EXISTS alerte (
    id          BIGSERIAL PRIMARY KEY,
    t           TIMESTAMPTZ NOT NULL DEFAULT now(),
    gravite     TEXT NOT NULL,
    sujet       TEXT NOT NULL,
    detail      TEXT,
    traitee     BOOLEAN DEFAULT FALSE
);
