"""Scenario-specific CO2 origins for syngas methanol, MTO/MTA and ethylene oxide.

Does not edit the workbook, uptake, captured quantities, allocation or utilities.
Only explicitly configured process boundaries are eligible. Unknown origins
stay fossil-labelled, with coverage and physical residuals reported separately.
"""
from __future__ import annotations

from copy import deepcopy
from functools import wraps
import hashlib
import json
import math
import re
from pathlib import Path
from uuid import uuid4

import numpy as np
import pandas as pd
import yaml

from carbon_accounting import _itom_formula_fraction

ROOT = Path(__file__).resolve().parent
POLICY = ROOT / "configuration_file/por_carbon_emissions.yaml"
MARKER = "por carbon emissions"
ORIGINS = ("fossil", "biogenic", "dac", "unknown")
ADAPTER_VERSION = 5
# Absolute share tolerance only for a declared provider present on one side of
# the comparison. xarray's interpolation can return ~1e-18 at a zero endpoint.
# Never remove or round that exchange in the inventory/origin tracer.
ZERO_SHARE_ATOL = 1e-12


def _allocated_family(dataset):
    """Reviewed NL process family, independent of its allocated output/unit."""
    if dataset.get("location", "NL") != "NL":
        return None
    match = re.search(r"(?:MTO with loop|MTA retrofit), mode ([12]), stage-based, "
                      r"(energy|economic) allocation \(por\)$", dataset.get("name", ""))
    if not match:
        return None
    technology = "MTO" if match.group().startswith("MTO") else "MTA"
    return technology + "_" + match[1], match[2]


def _process_rule(dataset, policy):
    family = _allocated_family(dataset)
    if family:
        return family[0], {"basis": "allocated_carbon_inputs", "allowed_carbon_inputs":
                          ["methanol", "syngas, CO component (por)", "syngas, CO2 component (por)"]}
    matches = [(g, p) for g, p in policy["processes"].items() if p["name_fragment"] in dataset["name"]]
    if len(matches) > 1:
        raise ValueError(f"Ambiguous carbon process rules: {dataset['name']}")
    return matches[0] if matches else (None, None)


def _process_balance(ds, supply, process):
    """Use the same physical boundary for emission splitting and output origins."""
    inputs = supply.carbon_inputs(ds)
    reference_basis = process.get("basis") == "reference_product"
    allowed = process.get("allowed_carbon_inputs", ["methanol", "syngas, CO2 component (por)"])
    if not inputs or any(e.get("product", e.get("reference product")) not in allowed for e, _ in inputs):
        raise ValueError(f"Missing or unreviewed carbon input in {ds['name']}")
    if any(e.get("type") == "technosphere" and float(e.get("amount", 0)) > 0
           and any(s in e.get("name", "") for s in ("market for natural gas", "market for coal", "market for fuel oil"))
           for e in ds.get("exchanges", ())):
        raise ValueError(f"New direct auxiliary fuel requires decomposition: {ds['name']}")
    if process.get("basis") == "allocated_carbon_inputs":
        # Allocation cancels in the input-carbon proportions. Physical closure
        # is documented externally; do not reconstruct it using mass yields.
        if any(not math.isfinite(float(e.get("amount", 0))) for e in ds.get("exchanges", ())
               if e.get("type") == "technosphere" and e.get("product", e.get("reference product")) in allowed):
            raise ValueError(f"Non-finite allocated carbon input: {ds['name']}")
        return dict(inputs=inputs, allocated_carbon=sum(c for _, c in inputs),
                    blocked=False, basis=f"one {ds['unit']} allocated output")
    factor = 1. if reference_basis else sum(process["yields"].values())
    if reference_basis:
        if ds.get("reference product") != process["reference_product"] or ds.get("unit") != "kilogram":
            raise ValueError(f"Reference-product basis changed in {ds['name']}")
        production = [e for e in ds.get("exchanges", ()) if e.get("type") == "production"
                      and e.get("product", e.get("reference product", e.get("name"))) == ds["reference product"]]
        if production and (len(production) != 1 or not math.isclose(float(production[0]["amount"]), 1., rel_tol=1e-9)):
            raise ValueError(f"Process inventory must produce one kg per reference unit: {ds['name']}")
    else:
        methanol = sum(float(e["amount"]) for e, _ in inputs if e.get("product") == "methanol")
        if not math.isclose(methanol * factor, 1., rel_tol=0.001):
            raise ValueError(f"Allocation/recipe basis changed in {ds['name']}")
    emitted = sum(float(e["amount"]) for e in ds.get("exchanges", ()) if e.get("type") == "biosphere"
                  and e.get("name") in ("Carbon dioxide, fossil", supply.policy["nonfossil_flow"]))
    captured = sum(float(e["amount"]) for e in ds.get("exchanges", ()) if e.get("type") == "technosphere"
                   and e.get("name") == "carbon dioxide compression, transport and storage" and float(e["amount"]) > 0)
    allocated_carbon = sum(c for _, c in inputs)
    incoming = allocated_carbon * factor
    products = sum(value * supply.fractions[product] for product, value in process["yields"].items())
    other_carbon = 0.
    uncertain_carbon = 0.
    indicators = []
    for exchange in ds.get("exchanges", ()):
        if exchange.get("type") != "biosphere":
            continue
        name = exchange.get("name")
        formula = process.get("biosphere_carbon_formulas", {}).get(name)
        indicator = name in process.get("organic_carbon_indicators", ())
        unresolved = name in process.get("unresolved_carbon_emissions", ())
        if not (formula or indicator or unresolved):
            continue
        amount = float(exchange["amount"])
        if exchange.get("unit") != "kilogram" or not math.isfinite(amount) or amount < 0:
            raise ValueError(f"Invalid carbon-bearing emission in {ds['name']}: {name}")
        if formula:
            other_carbon += amount * _itom_formula_fraction(formula)
        elif indicator:
            indicators.append(amount)
        else:
            # An unspecified organic mixture has a carbon fraction in [0,1].
            uncertain_carbon += amount
    if indicators:
        other_carbon += max(indicators)
        uncertain_carbon += sum(indicators) - max(indicators)
    outgoing = products + (emitted + captured) * factor * _itom_formula_fraction("CO2") + other_carbon * factor
    residual = incoming - outgoing
    residual_min = residual - uncertain_carbon * factor
    if residual_min < -supply.policy["balance_relative_tolerance"] * incoming:
        raise ValueError(f"Carbon output exceeds input for {ds['name']}: {residual_min:g} kg C per process basis")
    failed = residual > supply.policy["maximum_positive_residual_fraction"] * incoming
    if failed and process.get("on_positive_balance_failure") != "retain_fossil_and_unknown_output":
        raise ValueError(f"Unresolved carbon residual exceeds acceptance bound for {ds['name']}; review the recipe.")
    return dict(inputs=inputs, factor=factor, allocated_carbon=allocated_carbon, incoming=incoming,
                outgoing=outgoing, residual=residual, residual_min=residual_min,
                other_carbon=other_carbon * factor, uncertain_carbon=uncertain_carbon * factor,
                captured=captured, emitted=emitted, blocked=failed,
                basis=f"kg {process['reference_product']} output" if reference_basis else "kg external methanol input")


def _key(ds):
    return (ds.get("name"), ds.get("reference product", ds.get("product")),
            ds.get("unit"), ds.get("location"))


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_carbon_spec(scenario, policy_path=POLICY):
    """Bind the adapter to this external package, case, year and frozen inputs."""
    matches = []
    for entry in scenario.get("external scenarios", ()):
        package = entry.get("data")
        descriptor = getattr(package, "descriptor", {})
        if descriptor.get("name") == "itom_por":
            matches.append((entry, descriptor))
    if not matches:
        return None  # steel and other scopes never read the PoR inventory
    if len(matches) != 1:
        raise ValueError("Expected one ITOM-PetChem package per scenario.")
    entry, descriptor = matches[0]
    resources = {r["name"]: r for r in descriptor["resources"]}
    base = Path(getattr(entry["data"], "base_path", None) or ROOT)
    paths = {name: (base / resources[name]["path"]).resolve()
             for name in ("config", "scenario_data", "inventories")}
    paths["policy"] = Path(policy_path).resolve()
    policy = yaml.safe_load(paths["policy"].read_text(encoding="utf-8"))
    if policy.get("schema_version") != 1 or policy.get("unknown_origin") != "retain_fossil":
        raise ValueError("Unsupported carbon-emissions policy.")
    config = yaml.safe_load(paths["config"].read_text(encoding="utf-8"))
    table = pd.read_csv(paths["scenario_data"])
    table = table.loc[table.scenario.eq(entry["scenario"]) & table.region.eq("NL")]
    if table.empty or table.variables.duplicated().any():
        raise ValueError("Carbon adapter requires unique ITOM NL variables for the selected case.")
    years = sorted(int(c) for c in table if str(c).isdigit())
    year = int(scenario["year"])
    if not years or not years[0] <= year <= years[-1]:
        raise ValueError("Carbon-adapter year is outside the ITOM scenario data.")
    required = {p.get("production volume", {}).get("variable")
                for p in config.get("production pathways", {}).values()} - {None}
    volumes = {}
    for row in table.to_dict("records"):
        if row["variables"] not in required:
            continue
        values = np.asarray([row[str(y)] for y in years], dtype=float)
        if not np.isfinite(values).all() or (values < 0).any():
            raise ValueError(f"Invalid ITOM production volume: {row['variables']}")
        volumes[row["variables"]] = float(np.interp(year, years, values))
    active, declared = set(), set()
    for pathway in config.get("production pathways", {}).values():
        name = pathway["ecoinvent alias"]["name"]
        declared.add(name)
        variable = pathway.get("production volume", {}).get("variable")
        if variable is not None and variable not in volumes:
            raise ValueError(f"Missing ITOM volume for carbon adapter: {variable}")
        if variable is not None and volumes[variable] > 0:
            active.add(name)
    required_targets = []
    for dataset in config.get("regionalize", {}).get("datasets", ()):
        _, process = _process_rule(dataset, policy)
        if process and process.get("activation") == "regionalized":
            if dataset.get("reference product") != process["reference_product"]:
                raise ValueError(f"Regionalized carbon-process product mismatch: {dataset['name']}")
            declared.add(dataset["name"])
            active.add(dataset["name"])
            required_targets.append((dataset["name"], process["reference_product"], "kilogram", "NL"))
    expected_markets = {}
    for market in config["markets"]:
        if market["name"] not in ("market for methanol (por)", "market for syngas, CO component (por)",
                                   "market for syngas, CO2 component (por)", "market for ethylene (por)"):
            continue
        providers = {}
        for alias in market["includes"]:
            pathway = config["production pathways"][alias]
            amount = volumes[pathway["production volume"]["variable"]]
            name = pathway["ecoinvent alias"]["name"]
            providers[name] = providers.get(name, 0.) + amount
        total = sum(providers.values())
        if total:
            expected_markets[market["name"]] = dict(product=market["reference product"],
                providers={name: value / total for name, value in providers.items() if value > 0},
                declared_providers=sorted(providers))
    expected = expected_markets.get("market for methanol (por)", {}).get("providers", {})
    hashes = {str(p): _sha(p) for p in paths.values()}
    signature = hashlib.sha256(json.dumps({"adapter_version": ADAPTER_VERSION, "hashes": hashes, "case": entry["scenario"], "year": year}, sort_keys=True).encode()).hexdigest()
    return dict(policy=policy, active=active, declared=declared, expected_methanol=expected,
                expected_carbon_markets=expected_markets,
                required_targets=required_targets,
                case=entry["scenario"], year=year, source_sha256=hashes, signature=signature)


class CarbonSupply:
    """Read only carbon-relevant recipes, following actual provider identities.

    No origins are inferred from a CO2 emission label. Cycles without an
    independent anchor remain unknown; they never acquire a removal credit.
    """
    def __init__(self, identities, getter, policy):
        self.getter, self.policy = getter, policy
        self.identities, self.addresses = {}, {}
        for identity in identities:
            self.identities.setdefault(_key(identity), []).append(identity)
            if identity.get("code") is not None:
                self.addresses.setdefault((identity.get("database"), identity["code"]), []).append(identity)
        self.cache, self.unresolved = {}, set()
        self.fractions = {p: _itom_formula_fraction(f) for p, f in policy["carbon_formulas"].items()}

    def provider(self, exchange):
        matches = self.identities.get(_key(exchange), [])
        address = exchange.get("input")
        if address and tuple(address) in self.addresses:
            addressed = self.addresses[tuple(address)]
            if len(addressed) == 1 and (not matches or _key(addressed[0]) == _key(exchange)):
                matches = addressed
            else:
                raise ValueError(f"Carbon supplier address/identity mismatch: {_key(exchange)}")
        if len(matches) != 1:
            raise ValueError(f"No unique linked carbon supplier: {_key(exchange)}")
        return self.getter(matches[0])

    def carbon_inputs(self, dataset):
        return [(e, float(e["amount"]) * self.fractions[e.get("product", e.get("reference product"))])
                for e in dataset.get("exchanges", ()) if e.get("type") == "technosphere"
                and e.get("unit") == "kilogram" and float(e.get("amount", 0)) > 0
                and e.get("product", e.get("reference product")) in self.fractions
                and self.fractions[e.get("product", e.get("reference product"))] > 0]

    def origins(self, dataset, visiting=()):
        key = _key(dataset)
        if key in self.cache:
            return self.cache[key].copy()
        unknown = np.array([0., 0., 0., 1.])
        if key in visiting:
            self.unresolved.add((key, "cyclic_carbon_supply"))
            return unknown
        for rule in self.policy["source_origins"]:
            if dataset["name"] == rule["name"] and dataset["reference product"] == rule["product"]:
                result = np.eye(4)[ORIGINS.index(rule["origin"])]
                self.cache[key] = result
                return result.copy()
        inputs = self.carbon_inputs(dataset)
        product = dataset["reference product"]
        is_market = dataset["name"].startswith(("market for ", "market group for "))
        # Markets/interfaces pass the same physical product. Synthesis may
        # transform explicitly quantified CO and CO2 carbon into methanol.
        if is_market or "supply placeholder (por)" in dataset["name"] or ", imported from EU (por)" in dataset["name"]:
            inputs = [(e, c) for e, c in inputs if e.get("product", e.get("reference product")) == product]
        else:
            _, process = _process_rule(dataset, self.policy)
            if process:
                balance = _process_balance(dataset, self, process)
                if balance["blocked"]:
                    self.unresolved.add((key, "methanol_carbon_balance_review_required" if product == "methanol"
                                         else "process_carbon_balance_review_required"))
                    return unknown
            else:
                inputs = []  # unreviewed waste/gasification recipes remain unknown
        if not inputs:
            self.unresolved.add((key, "unreviewed_carbon_source"))
            return unknown
        total = sum(c for _, c in inputs)
        result = sum((c * self.origins(self.provider(e), (*visiting, key)) for e, c in inputs), np.zeros(4)) / total
        # Don't cache a result dependent on a traversal-specific cycle cut.
        if not visiting:
            self.cache[key] = result
        return result


def _scaled(exchange, fraction):
    """Scale an existing distribution; leave shape/correlation parameters intact."""
    result = deepcopy(exchange)
    result["amount"] = float(exchange["amount"]) * fraction
    kind = exchange.get("uncertainty type", 0)
    if kind not in (0, 1, 2, 3, 4, 5):
        raise ValueError(f"Unreviewed uncertainty distribution for CO2 split: {kind}")
    if exchange.get("loc") is not None:
        result["loc"] = (float(exchange["loc"]) + math.log(fraction) if kind == 2
                         else float(exchange["loc"]) * fraction)
    if kind in (3, 4) and exchange.get("scale") is not None:
        result["scale"] = float(exchange["scale"]) * fraction
    for field in ("minimum", "maximum"):
        if exchange.get(field) is not None:
            result[field] = float(exchange[field]) * fraction
    return result


def plan_carbon_emissions(identities, getter, spec, biosphere_codes, *, biosphere_name=None):
    """Return replacements without mutating any scenario, activity or exchange."""
    policy = spec["policy"]
    supply = CarbonSupply(identities, getter, policy)
    missing_targets = set(map(tuple, spec.get("required_targets", ()))) - {_key(d) for d in identities}
    if missing_targets:
        raise ValueError(f"Missing regionalized carbon-correction activities: {sorted(missing_targets)}")
    declared_families = {_allocated_family(dict(name=name)) for name in spec["declared"]} - {None}
    active_families = {_allocated_family(dict(name=name)) for name in spec["active"]} - {None}
    def active(dataset):
        family = _allocated_family(dataset)
        return family in active_families if family else dataset["name"] in spec["active"]
    targets = [d for d in identities if d.get("location") == "NL"
               and (d.get("name") in spec["declared"] or _allocated_family(d) in declared_families)
               and (_process_rule(d, policy)[1] is not None or
                    any(x in d["name"].lower() for x in ("mto", "mta", "methanol-to-olefins")))]
    replacements, audit, joint_recipes, allocated_shares = {}, [], {}, {}
    # Verify that the live methanol market really represents the selected ITOM
    # case/year rather than a stale or unmodified background market.
    if any(active(t) for t in targets):
        markets = spec.get("expected_carbon_markets", {"market for methanol (por)":
            dict(product="methanol", providers=spec["expected_methanol"])})
        for market_name, expected in markets.items():
            market = supply.provider(dict(name=market_name, product=expected["product"], unit="kilogram", location="NL"))
            actual = {}
            for e, _ in supply.carbon_inputs(market):
                if e.get("product") == expected["product"]:
                    actual[e["name"]] = actual.get(e["name"], 0.) + float(e["amount"])
            total = sum(actual.values())
            if not math.isclose(total, 1., rel_tol=1e-5, abs_tol=1e-8):
                raise ValueError(f"Carbon-market supply does not sum to one: {market_name}")
            validate_carbon_market_shares(actual, expected, market_name)
    for identity in targets:
        ds = getter(identity)
        row = dict(activity=list(_key(ds)), case=spec["case"], year=spec["year"])
        if not active(ds):
            audit.append(dict(row, status="inactive_pathway")); continue
        group, process = _process_rule(ds, policy)
        if process is None:
            audit.append(dict(row, status="legacy_recipe_review_required",
                              reason="Feedstock/fuel decomposition and complete coproduct yields not reviewed.")); continue
        if ds.get(MARKER):
            if ds[MARKER]["signature"] != spec["signature"]:
                raise ValueError("Already adapted inventory has a different carbon-policy/source signature; rebuild it.")
            audit.append(dict(row, **ds[MARKER]["audit"], status="already_applied")); continue
        exchanges = ds.get("exchanges", [])
        allocated = process.get("basis") == "allocated_carbon_inputs"
        emissions = [e for e in exchanges if e.get("type") == "biosphere" and e.get("name") == "Carbon dioxide, fossil"]
        if (not emissions and not allocated) or any(not math.isfinite(float(e["amount"])) or float(e["amount"]) < 0 for e in emissions):
            raise ValueError(f"Missing/negative process CO2 in {ds['name']}")
        if any(e.get("type") == "biosphere" and e.get("name") == policy["nonfossil_flow"] for e in exchanges):
            raise ValueError(f"Unmarked pre-existing non-fossil CO2 in {ds['name']}; review before adapting.")
        if allocated and any(not math.isfinite(float(e.get("amount", 0))) for e in exchanges
                             if e.get("type") == "technosphere" and e.get("product", e.get("reference product")) in process['allowed_carbon_inputs']):
            raise ValueError(f"Non-finite allocated carbon input: {ds['name']}")
        if allocated and not supply.carbon_inputs(ds):
            if any(float(e["amount"]) for e in emissions):
                raise ValueError(f"Direct CO2 without allocated carbon inputs: {ds['name']}")
            audit.append(dict(row, process=group, status="zero_allocated_carbon", balance_status="documented_externally_not_reconstructed"))
            continue
        balance = _process_balance(ds, supply, process)
        inputs, allocated_carbon = balance["inputs"], balance["allocated_carbon"]
        shares = sum((c * supply.origins(supply.provider(e)) for e, c in inputs), np.zeros(4)) / allocated_carbon
        if not np.isfinite(shares).all() or (shares < 0).any() or not np.isclose(shares.sum(), 1.):
            raise ValueError("Invalid carbon-origin fractions.")
        if allocated:
            family = _allocated_family(ds)
            if family in allocated_shares and not np.allclose(shares, allocated_shares[family], rtol=1e-8, atol=1e-10):
                raise ValueError(f"Inconsistent input carbon origins across allocated outputs for {family}")
            allocated_shares[family] = shares
            emission_shares = shares
            emitted = sum(float(e["amount"]) for e in emissions)
            nonfossil = min(1., max(0., float(shares[1] + shares[2])))
            captured = sum(float(e["amount"]) for e in exchanges if e.get("type") == "technosphere"
                           and e.get("name") == "carbon dioxide compression, transport and storage" and float(e["amount"]) > 0)
            row.update(process=group, allocation=family[1],
                       status="no_direct_CO2" if not emitted else "split" if nonfossil else "no_known_nonfossil_carbon",
                       origin_fractions=dict(zip(ORIGINS, map(float, shares))),
                       emission_origin_fractions=dict(zip(ORIGINS, map(float, shares))),
                       process_basis=balance["basis"], incoming_kgC_per_reference_unit=allocated_carbon,
                       balance_status="documented_externally_not_reconstructed",
                       total_CO2=emitted, fossil_label_CO2=emitted*(1-nonfossil),
                       nonfossil_CO2=emitted*nonfossil, captured_CO2_unchanged=captured)
        else:
            material_mass = balance["factor"]
            incoming, outgoing, residual = balance["incoming"], balance["outgoing"], balance["residual"]
            emitted, captured = balance["emitted"], balance["captured"]
            joint = np.asarray([incoming, emitted * material_mass, captured * material_mass,
                                balance["other_carbon"], balance["uncertain_carbon"], *shares])
            if group in joint_recipes and not np.allclose(joint, joint_recipes[group], rtol=1e-8, atol=1e-10):
                raise ValueError(f"Inconsistent allocated coproduct recipes for {group}; review before splitting.")
            joint_recipes[group] = joint
            tolerance = policy["balance_relative_tolerance"] * incoming
            emission_shares = np.array([0., 0., 0., 1.]) if balance["blocked"] else shares
            nonfossil = min(1., max(0., float(emission_shares[1] + emission_shares[2])))
            row.update(process=group, status="balance_review_required" if balance["blocked"] else "split" if nonfossil else "no_known_nonfossil_carbon",
                       origin_fractions=dict(zip(ORIGINS, map(float, shares))),
                       emission_origin_fractions=dict(zip(ORIGINS, map(float, emission_shares))),
                       process_basis=balance["basis"],
                       incoming_kgC_per_process_basis=incoming, outgoing_kgC_per_process_basis=outgoing,
                       residual_kgC_per_process_basis=residual,
                       residual_min_kgC_per_process_basis=balance["residual_min"],
                       other_emitted_carbon_kgC_per_process_basis=balance["other_carbon"],
                       uncertain_emitted_carbon_upper_kgC_per_process_basis=balance["uncertain_carbon"],
                       residual_fraction=residual / incoming,
                       balance_status="positive_residual_exceeds_acceptance_bound" if balance["blocked"] else "balanced_within_tolerance" if abs(residual) <= tolerance else "unresolved_positive_residual",
                       total_CO2=emitted, fossil_label_CO2=emitted * (1-nonfossil),
                       nonfossil_CO2=emitted * nonfossil, captured_CO2_unchanged=captured)
            if process.get("basis") != "reference_product" or process.get("reference_product") == "methanol":
                # Preserve old report fields only where their methanol basis applies.
                row.update(incoming_kgC_per_kg_methanol=incoming, outgoing_kgC_per_kg_methanol=outgoing,
                           residual_kgC_per_kg_methanol=residual)
        audit.append(row)
        if not nonfossil or (allocated and not emitted):
            continue
        changed = deepcopy(ds)
        updated = []
        for exchange in exchanges:
            if exchange not in emissions:
                updated.append(deepcopy(exchange)); continue
            if exchange.get("unit") != "kilogram":
                raise ValueError("CO2 biosphere exchange must be in kilograms.")
            if nonfossil < 1:
                updated.append(_scaled(exchange, 1-nonfossil))
            new = _scaled(exchange, nonfossil)
            categories = exchange.get("categories", ("air",))
            if isinstance(categories, str):
                categories = tuple(categories.split("::"))
            if not categories or categories[0] != "air":
                raise ValueError("Expected atmospheric CO2 release compartment.")
            code_key = (policy["nonfossil_flow"], categories[0], categories[1] if len(categories)>1 else "unspecified", "kilogram")
            original_input = exchange.get("input")
            target_biosphere = biosphere_name or (original_input[0] if original_input else None)
            if code_key not in biosphere_codes or not target_biosphere:
                raise ValueError(f"Missing linked non-fossil biosphere identity: {code_key}")
            new["name"] = policy["nonfossil_flow"]
            # Native background recipes may intentionally omit biosphere input
            # addresses until export. Resolve those through the builder's
            # configured biosphere, not a guessed database or flow compartment.
            new["input"] = (target_biosphere, biosphere_codes[code_key])
            for stale in ("code", "id", "flow", "flow_id", "output"):
                new.pop(stale, None)
            new["comment"] = str(new.get("comment", "")) + " Scenario carbon-origin split; DAC and biogenic shares are separate in the PoR carbon audit. No additional uptake credit."
            updated.append(new)
        if not math.isclose(sum(float(e["amount"]) for e in updated if e.get("type") == "biosphere"
                               and e.get("name") in ("Carbon dioxide, fossil", policy["nonfossil_flow"])), emitted, rel_tol=1e-12):
            raise ValueError("CO2 mass changed during origin splitting.")
        changed["exchanges"] = updated
        changed[MARKER] = dict(signature=spec["signature"], audit={k:v for k,v in row.items() if k not in ("activity", "case", "year", "status")})
        replacements[_key(ds)] = changed
    return replacements, dict(adapter_version=ADAPTER_VERSION, case=spec["case"], year=spec["year"], signature=spec["signature"],
        source_sha256=spec["source_sha256"], changed_activities=len(replacements), activities=audit,
        balance_reviews=sum(r["status"] == "balance_review_required" for r in audit),
        unresolved_suppliers=[dict(activity=list(k), reason=r) for k,r in sorted(supply.unresolved)],
        assumptions="Homogeneous carbon mixing; unresolved origins retain fossil label; positive residuals are reported, not closed artificially; no extra uptake or storage credit.")


def validate_carbon_market_shares(actual, expected, market_name):
    """Compare weights, permitting only declared numerical-zero membership gaps.

    Existing nonzero-weight tolerances stay unchanged. Declared zero providers
    use a separate, much tighter absolute bound. Unknown suppliers are rejected
    regardless of size, and no actual supplier weight is modified here.
    """
    wanted = expected["providers"]
    declared = set(expected.get("declared_providers", wanted))
    unexpected = sorted(set(actual) - declared)
    differences = []
    for name in sorted(set(actual) | set(wanted)):
        found, required = actual.get(name, 0.), wanted.get(name, 0.)
        membership_differs = name not in actual or name not in wanted
        matches = (math.isclose(found, required, rel_tol=0., abs_tol=ZERO_SHARE_ATOL)
                   if membership_differs else math.isclose(found, required, rel_tol=1e-4, abs_tol=1e-7))
        if not matches:
            differences.append(f"{name}: actual={found:.12g}, expected={required:.12g}")
    if unexpected or differences:
        details = "; ".join(([f"undeclared suppliers={unexpected}"] if unexpected else []) + differences)
        raise ValueError(f"Linked carbon market differs from this ITOM scenario/year: {market_name}. {details}")


def apply_por_carbon_emissions(builder, *, policy_path=POLICY, report_dir=ROOT / "export/por_carbon_emissions",
                               stage="export", load_database_func=None, dump_database_func=None,
                               biosphere_codes=None, verbose=True, expected_signatures=None):
    """Stage all scenarios, certify native-store edits, then publish together."""
    import por_supplier_linking as linking
    from premise_compat import open_inventory_store, iter_activity_metadata
    specs = [load_carbon_spec(scene, policy_path) for scene in builder.scenarios]
    signatures = [s["signature"] if s else None for s in specs]
    if expected_signatures is not None and signatures != expected_signatures:
        raise ValueError("Carbon adapter inputs changed during premise.update(); rebuild the scenarios.")
    if not any(specs):
        return []
    if biosphere_codes is None:
        from premise.export import biosphere_flows_dictionary
        biosphere_codes = biosphere_flows_dictionary(str(builder.version))
    registry = getattr(builder, "_validation_reports", None)
    saved = dict(registry) if isinstance(registry, dict) else None
    pending = []
    directory = Path(report_dir)
    directory.mkdir(parents=True, exist_ok=True)
    try:
        for scene, spec in zip(builder.scenarios, specs):
            if spec is None:
                continue
            if verbose:
                print(f"PoR carbon origins — {spec['year']} {spec['case']}: checking syngas methanol, MTO/MTA and ethylene oxide", flush=True)
            if "external" not in scene.get("applied functions", ()):
                raise ValueError("Carbon adapter requires completed external scenario updates and supplier linking.")
            working = dict(scene)
            native = linking._has_inventory_store(scene)
            if native:
                api = linking._inventory_store_api()
                source = open_inventory_store(scene, api)
                records = list(iter_activity_metadata(source))
                ids = {_key(meta): aid for aid, meta in records}
                identities = [meta for _, meta in records]
                cache = {}
                def getter(meta):
                    key = _key(meta)
                    if key not in cache:
                        cache[key] = source.activity(ids[key]).to_dict()
                    return cache[key]
            else:
                if working.get("database") is None:
                    if not working.get("database filepath"):
                        raise ValueError("Updated carbon inventory is missing; reference fallback is forbidden.")
                    if load_database_func is None:
                        from premise.utils import load_database as load_database_func
                    working = load_database_func(scenario=working, original_database=[], delete=False, load_metadata=False, warning=False)
                identities = working["database"]
                getter = lambda ds: ds
            replacements, receipt = plan_carbon_emissions(identities, getter, spec, biosphere_codes,
                biosphere_name=getattr(builder, "biosphere_name", None))
            if replacements and native:
                validation = linking._inventory_validation_api()
                fingerprints, cycles = validation.inventory_baseline_snapshot(source)
                staged = source.fork(scenario_identity=source.scenario_identity)
                with staged.transaction("repo:por_carbon_emissions") as tx:
                    for key, ds in replacements.items():
                        tx.patch_activity(ids[key], {"exchanges": ds["exchanges"], MARKER: ds[MARKER]})
                for field in ("database", "database filepath", "database metadata filepath", "_inventory_checkpoint", "_inventory_export_handoff", "_inventory_working_copy"):
                    working.pop(field, None)
                working["_inventory_store"] = staged
                intent = validation.ValidationIntent(transformation="repo_por_carbon_emissions", targeted=True,
                    affected_activity_ids=frozenset(ids[k] for k in replacements), expected_match_count=len(replacements),
                    baseline_fingerprints=fingerprints, baseline_cycles=cycles)
                original_records = [getter(meta) if _key(meta) in replacements else meta for meta in identities]
                linking._certify_linked_store(builder, working, staged, validation, intent,
                    [aid for aid, _ in records], original_records, receipt)
                persisted = scene.get("_inventory_checkpoint") is not None
                checkpoint = None
                if persisted:
                    if hasattr(staged, "_scenario_cache_compatibility"):
                        staged._scenario_cache_compatibility = False
                    destination = directory / "inventory_stores" / f"carbon_{uuid4().hex}.inventory-store"
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    checkpoint = staged.checkpoint(destination)
                    if checkpoint is None:
                        raise ValueError("Adapted carbon inventory checkpoint was not saved.")
                    working["_inventory_checkpoint"] = checkpoint
                if scene.get("mapping"):
                    hydrate = getattr(api, "_hydrate_scenario_mapping", None)
                    compact = getattr(api, "_compact_scenario_mapping", None)
                    activities = getattr(getattr(staged, "_state", None), "activities", None)
                    if not callable(hydrate) or activities is None or (persisted and not callable(compact)):
                        raise ValueError("Cannot rebind the adapted scenario activity mapping.")
                    mapping = hydrate(scene["mapping"], activities)
                    working["mapping"] = compact(mapping, staged, checkpoint) if persisted else mapping
                if persisted:
                    working.pop("_inventory_store", None)
            elif replacements:
                working["database"] = [replacements.get(_key(ds), ds) for ds in identities]
                if scene.get("database filepath"):
                    if dump_database_func is None:
                        from premise.utils import dump_database as dump_database_func
                    # A new cache path is required: never overwrite the original.
                    working.pop("database filepath", None)
                    dump_database_func(working)
                    if working.get("database") is not None or not working.get("database filepath"):
                        raise ValueError("Adapted legacy carbon inventory was not persisted.")
            if not replacements:
                working = dict(scene)
            pending.append((scene, working, receipt))
        for spec in specs:
            if spec and any(_sha(path) != digest for path,digest in spec["source_sha256"].items()):
                raise ValueError("Carbon adapter inputs changed during the update; nothing published.")
        for scene, working, receipt in pending:
            path = directory / f"{stage}_{receipt['case']}_{receipt['year']}_{uuid4().hex}.json"
            receipt.update(stage=stage, report_path=str(path.resolve()))
            path.write_text(json.dumps(receipt, indent=2, allow_nan=False), encoding="utf-8")
            working[MARKER] = receipt
        for scene, working, _ in pending:
            scene.clear(); scene.update(working)
    except BaseException:
        if saved is not None:
            registry.clear(); registry.update(saved)
        elif hasattr(builder, "_validation_reports"):
            del builder._validation_reports
        raise
    reports = [receipt for _,_,receipt in pending]
    if verbose:
        reviews = sum(a["status"] == "balance_review_required" for r in reports for a in r["activities"])
        print(f"PoR carbon origins: {sum(r['changed_activities'] for r in reports)} inventories adapted; {reviews} balance review(s); reports: {directory}", flush=True)
    return reports


def attach_por_carbon_emissions(builder, **options):
    """Attach after PoR linking and before Pathways preflight or BW export."""
    if getattr(builder, "_por_carbon_emissions_attached", False):
        return False
    original = builder.update
    @wraps(original)
    def update(*args, **kwargs):
        specs = [load_carbon_spec(s, options.get("policy_path", POLICY)) for s in builder.scenarios]
        signatures = [s["signature"] if s else None for s in specs]
        result = original(*args, **kwargs)
        builder.por_carbon_emission_reports = apply_por_carbon_emissions(builder, expected_signatures=signatures, **options)
        return result
    builder.update = update
    builder._por_carbon_emissions_attached = True
    for name in ("write_db_to_brightway", "write_db_to_matrices"):
        original_export = getattr(builder, name, None)
        if callable(original_export):
            def guard(function):
                @wraps(function)
                def export(*args, **kwargs):
                    verify_carbon_reports(builder)
                    return function(*args, **kwargs)
                return export
            setattr(builder, name, guard(original_export))
    return True


def verify_carbon_reports(builder):
    for scene in builder.scenarios:
        receipt = scene.get(MARKER)
        required = any(getattr(entry.get("data"), "descriptor", {}).get("name") == "itom_por"
                       for entry in scene.get("external scenarios", ()))
        if required and receipt is None:
            raise ValueError("MTO/MTA carbon-origin preflight did not run; call update() before export.")
        if receipt and any(_sha(path) != digest for path,digest in receipt["source_sha256"].items()):
            raise ValueError("Carbon-adapter sources changed after update; recreate the builder before export.")


def attach_carbon_package_provenance(package):
    """Archive the scenario carbon receipts alongside the matrix resources."""
    if getattr(package, "_por_carbon_provenance_attached", False):
        return False
    original = package._build_datapackage
    @wraps(original)
    def build(*args, **kwargs):
        verify_carbon_reports(package.datapackage)
        reports = getattr(package.datapackage, "por_carbon_emission_reports", None)
        if reports is None:
            raise ValueError("Carbon-origin adapter did not run before package export.")
        path = Path.cwd() / "pathways_temp" / "por_carbon_emissions.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema_version": 1, "scenario_reports": reports}, indent=2, allow_nan=False), encoding="utf-8")
        return original(*args, **kwargs)
    package._build_datapackage = build
    package._por_carbon_provenance_attached = True
    return True
