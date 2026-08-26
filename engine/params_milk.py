"""Parameters for the cow's-milk allergy model.

Every entry states where it came from. Read the `provenance` column before you
quote any result: MEASURED and DERIVED values are facts, CALIBRATED values were
fitted to reproduce a published clinical endpoint, and ASSUMED values are
modelling choices that should be swept, not trusted.

Calibration anchors — the three published numbers the CALIBRATED parameters were
fitted to reproduce, and which `validate.py` re-checks on every run:

  1. Eliciting dose ED05 for cow's milk = 0.2 mg total milk protein
     (Allergen Bureau VITAL 3.0; Remington et al. 2020, Food Chem Toxicol 139:111259)
  2. Milk OIT raises the eliciting dose roughly 100-fold over ~12 months
     (Skripak et al. 2008, J Allergy Clin Immunol 122:1154; Longo et al. 2008, JACI 121:343)
  3. Plasma histamine in severe systemic reactions reaches 10-15 ng/mL, with
     objective symptoms appearing from 2-3 ng/mL
     (Kaliner et al. 1982, JAMA 248:2534)
"""

from __future__ import annotations

from .provenance import Param, Provenance, Registry

M = Provenance.MEASURED
D = Provenance.DERIVED
C = Provenance.CALIBRATED
A = Provenance.ASSUMED


MILK = Registry(
    {
        # ------------------------------------------------------------------
        # Allergen: beta-lactoglobulin (Bos d 5), the dominant whey allergen
        # ------------------------------------------------------------------
        "blg_mw": Param(
            18300, "g/mol", M,
            "Brownlow et al. 1997, Structure 5:481",
            "Bovine beta-lactoglobulin monomer.",
        ),
        "blg_fraction": Param(
            0.10, "g/g", M,
            "Fox & McSweeney 1998, Dairy Chemistry and Biochemistry",
            "BLG is ~3.2 g/L of the ~33 g/L total protein in cow's milk.",
        ),
        # ------------------------------------------------------------------
        # IgE - allergen binding
        # ------------------------------------------------------------------
        "k_bind": Param(
            3.0e7, "1/M", M,
            "Christensen et al. 2008, J Biol Chem 283:29543",
            "Allergen-specific IgE Fab affinity, KD ~33 nM, inside the 1-100 nM range "
            "reported for food-allergen-specific IgE. This value also fixes where the "
            "prozone sits, at 1/(2*k_bind) = 17 nM, and so how much of the clinical "
            "dose range stays on the ascending limb.",
        ),
        "k_cross": Param(
            2.2e-4, "cell/receptor", C,
            "fitted to VITAL ED05 = 0.2 mg milk protein",
            "Surface crosslinking constant. Kx*R_total ~5, the physically expected order "
            "(Perelson & DeLisi 1980, Math Biosci 48:71).",
        ),
        "receptors_per_cell": Param(
            2.4e5, "receptors/cell", M,
            "Malveaux et al. 1978, J Clin Invest 62:176",
            "FcepsilonRI per human basophil/mast cell; reported range 1e5-5e5.",
        ),
        "k_occupancy_ku": Param(
            120.0, "kU/L", C,
            "fitted to FcepsilonRI occupancy vs serum IgE",
            "Total IgE at which FcepsilonRI is half-occupied. Receptor number itself "
            "is IgE-regulated (MacGlashan 1997, J Immunol 158:1438), lumped in here.",
        ),
        "crosslink_threshold": Param(
            100.0, "crosslinks/cell", M,
            "Fewtrell & Metzger 1980, J Immunol 125:701",
            "Roughly 100 receptor dimers suffice to trigger degranulation. Held "
            "fixed rather than fitted: it is degenerate with mucosal_cmax, since "
            "crosslink count only ever enters as a ratio against this threshold, "
            "and mucosal_cmax absorbs the scale.",
        ),
        "crosslink_hill": Param(
            2.0, "-", A,
            "modelling choice",
            "Cooperativity of the aggregation-to-degranulation step. Sweep this.",
        ),
        # ------------------------------------------------------------------
        # Mucosal delivery: lumen dose -> lamina propria concentration
        # ------------------------------------------------------------------
        "mucosal_cmax": Param(
            6.89949e-08, "M", C,
            "fitted to the VITAL population ED50 for cow's milk (25 mg protein)",
            "Saturating free BLG at the mucosal mast cell. Lumps epithelial "
            "permeability (P_app ~1e-7 cm/s for intact protein), exposed surface and "
            "local clearance into one number. Sits far below the crosslinking peak "
            "at 1/(2*k_bind) = 5 nM, which is why the clinical dose range stays on "
            "the ascending limb and the dose-response comes out monotone.",
        ),
        "mucosal_km": Param(
            5000.0, "mg protein", A,
            "modelling choice",
            "Dose at which mucosal transfer half-saturates. Deliberately placed "
            "above the clinical range so delivery stays near-linear in dose. An "
            "earlier version fitted this to 2.5 mg and the model then predicted that "
            "patients with milk-sIgE below ~10 kU/L could never react at any dose — "
            "saturation inside the clinical range removes the only lever a large "
            "dose has. Not fitted, because nothing here identifies it.",
        ),
        "recruit_km": Param(
            300.0, "mg protein", A,
            "modelling choice",
            "Dose at which half the mucosal mast cell pool is engaged — larger doses "
            "reach more gut surface rather than raising the local concentration. Not "
            "fitted: it is degenerate with histamine_yield, which absorbs the scale. "
            "Only its position relative to the clinical dose range matters.",
        ),
        # ------------------------------------------------------------------
        # Mast cell degranulation and histamine
        # ------------------------------------------------------------------
        "absorption_tmax": Param(
            600.0, "s", M,
            "Baumert et al. 2018, J Allergy Clin Immunol Pract 6:457",
            "Time of peak arrival rate of intact food allergen at the mucosa, gated "
            "by gastric emptying and intestinal transit. Serum allergen is detectable "
            "about 10 min after ingestion, which is why food reactions begin minutes "
            "rather than seconds after a meal.",
        ),
        "k_degranulate": Param(
            0.012, "1/s", M,
            "Fewtrell & Metzger 1980, J Immunol 125:701",
            "Degranulation is near-complete within 60-90 s of crosslinking.",
        ),
        "histamine_yield": Param(
            28.6331, "ng/mL", C,
            "fitted to Kaliner et al. 1982 severe-reaction plasma histamine",
            "Plasma histamine if the entire mucosal mast cell pool degranulates. "
            "Lumps 3 pg histamine/cell, mast cell mass and tissue-to-plasma transfer "
            "into one number. Calibrated so a glass of milk in a typical patient "
            "peaks at 12 ng/mL, the measured severe-reaction level.",
        ),
        "histamine_halflife": Param(
            90.0, "s", M,
            "Beaven et al. 1972, Br J Pharmacol 44:283",
            "Plasma histamine half-life, 1-2 min via diamine oxidase and HNMT.",
        ),
        "histamine_baseline": Param(
            0.5, "ng/mL", M,
            "Kaliner et al. 1982, JAMA 248:2534",
            "Resting plasma histamine, reported 0.3-1.0 ng/mL.",
        ),
        # ------------------------------------------------------------------
        # Symptom scale (CoFAR/PRACTALL-style 0-10)
        # ------------------------------------------------------------------
        "symptom_ec50": Param(
            4.0, "ng/mL", M,
            "Kaliner et al. 1982, JAMA 248:2534",
            "Flushing at 1-2 ng/mL, tachycardia and GI symptoms at 3-5, severe >10.",
        ),
        "symptom_hill": Param(
            1.8, "-", C,
            "fitted to the Kaliner histamine-symptom series",
            "",
        ),
        "symptom_max": Param(
            10.0, "score", A,
            "scale definition",
            "Top of the 0-10 severity scale used throughout.",
        ),
        "reaction_threshold": Param(
            3.0, "score", M,
            "Sampson et al. 2012 PRACTALL, J Allergy Clin Immunol 130:1260",
            "Objective symptoms — the point a food challenge is called positive.",
        ),
        "local_weight": Param(
            0.55, "-", A,
            "modelling choice",
            "Ceiling of the local/GI symptom channel as a fraction of the full "
            "scale. Mucosal degranulation drives vomiting and abdominal pain "
            "through enteric reflexes without systemic histamine, but on its own "
            "does not produce cardiovascular collapse — so it tops out above the "
            "objective-symptom threshold and below anaphylaxis.",
        ),
        # ------------------------------------------------------------------
        # Blocking IgG4
        # ------------------------------------------------------------------
        "k_igg4": Param(
            1.0e7, "1/M", M,
            "Aalberse & Schuurman 2002, Immunology 105:9",
            "Milk-specific IgG4 affinity, KD ~100 nM — about 10x weaker than IgE, "
            "which is why it only blocks once its concentration is far higher.",
        ),
        "igg4_mw": Param(
            146000, "g/mol", M,
            "Vidarsson et al. 2014, Front Immunol 5:520",
            "IgG4 molecular weight, used to report titres in the mg/L units clinical "
            "labs actually issue.",
        ),
        "igg4_baseline": Param(
            2.0e-9, "M", M,
            "Savilahti et al. 2010, J Allergy Clin Immunol 125:1315",
            "Milk-specific IgG4 before immunotherapy, ~0.3 mg/L.",
        ),
        "igg4_halflife": Param(
            21.0, "day", M,
            "Vidarsson et al. 2014, Front Immunol 5:520",
            "Serum half-life of the IgG4 subclass.",
        ),
        "igg4_kprod": Param(
            1.8898e-8, "M/day", C,
            "fitted to the measured post-OIT milk-specific IgG4 titre of 60 mg/L",
            "Maximal IgG4 production under sustained antigen exposure. Fitted against "
            "the measured titre rather than against the threshold shift, so that the "
            "split of protection between blocking antibody and Treg comes out of the "
            "model instead of being assumed by the calibration.",
        ),
        "igg4_dose_km": Param(
            120.0, "mg/day", C,
            "fitted to milk OIT maintenance dosing (Skripak 2008)",
            "Daily dose at which IgG4 induction is half-maximal.",
        ),
        # ------------------------------------------------------------------
        # Specific IgE and regulatory T cells (slow, immunotherapy timescale)
        # ------------------------------------------------------------------
        "sige_halflife": Param(
            2.5, "day", M,
            "Vidarsson et al. 2014, Front Immunol 5:520",
            "Free serum IgE half-life. Receptor-bound IgE persists for weeks.",
        ),
        "total_ige_ku": Param(
            420.0, "kU/L", M,
            "Savilahti et al. 2010, J Allergy Clin Immunol 125:1315",
            "Total serum IgE in a typical milk-allergic child on OIT entry.",
        ),
        "treg_kind": Param(
            0.0640061, "1/day", C,
            "fitted to the 100x eliciting-dose shift over 12 months of milk OIT",
            "Induction rate of allergen-specific Treg under sustained exposure "
            "(Shreffler et al. 2009, J Allergy Clin Immunol 123:43). Fitted last, so "
            "it absorbs whatever protection the measured IgG4 titre does not explain.",
        ),
        "treg_halflife": Param(
            60.0, "day", C,
            "fitted to loss of desensitisation after OIT is stopped",
            "Decay of induced tolerance once antigen exposure ends. This is why "
            "desensitisation is not the same as sustained unresponsiveness.",
        ),
        "treg_dose_km": Param(
            80.0, "mg/day", C,
            "fitted to milk OIT maintenance dosing",
            "",
        ),
        "treg_suppression": Param(
            0.75, "-", C,
            "fitted to the sIgE decline seen late in OIT",
            "Maximum fractional suppression of IgE production by induced Treg.",
        ),
    },
    name="MILK",
)


def milk_protein_to_blg_molar(mg_protein: float, volume_l: float = 1.0) -> float:
    """Convert mg of total milk protein to mol/L of beta-lactoglobulin."""
    grams_blg = mg_protein * 1e-3 * float(MILK.blg_fraction)
    return grams_blg / float(MILK.blg_mw) / volume_l
