"""Supplier-linking, cache-transaction and export-hook regression checks."""
from copy import deepcopy
import ast
from importlib.util import find_spec
from pathlib import Path
import pickle
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List
import uuid
import tempfile
import unittest

from por_supplier_linking import (
    PorLinkingPolicy, SupplierLinkingError, apply_por_supplier_linking,
    attach_por_supplier_linking, dataset_key, exchange_key, link_por_database,
    install_por_export_geography_guard, _register_policy,
    LINKING_POLICY_VERSION,
)
from premise_compat import disable_legacy_por_regionalization


def activity(name, product, location="NL", exchanges=None, unit="kilogram"):
    return {"name": name, "reference product": product, "location": location, "unit": unit,
            "code": name + "_" + location, "exchanges": exchanges or []}


def feed(name="market for methanol", product="methanol", location="RER", amount=2, unit="kilogram", **extra):
    return {"name": name, "product": product, "location": location, "unit": unit,
            "amount": amount, "type": "technosphere", **extra}


def policy(imports=False, profiles=None, additions=None):
    paths = {"FE_PPP": {"production volume": {"variable": "Final Product|polypropylene|PP"},
                        "ecoinvent alias": {"name": "PPP production", "reference product": "polypropylene"}}}
    if imports:
        paths["methanol_trade_import"] = {
            "production volume": {"variable": "Trade|To PoR|methanol"},
            "ecoinvent alias": {"name": "methanol, imported", "reference product": "methanol"}}
    markets = [{"name": "market for methanol (por)", "reference product": "methanol", "unit": "kilogram",
                "includes": [], "replaces": [{"name": "market for methanol", "product": "methanol"},
                                             {"name": "market for methanol (SPS)", "product": "methanol"}],
                "replaces in": [{"location": "NL"}], "add": additions or []}]
    return PorLinkingPolicy({"production pathways": paths, "markets": markets}, profiles or {})


def basic_database():
    return [activity("PPP production", "polypropylene", exchanges=[feed()]),
            activity("market for methanol", "methanol", "RER"),
            activity("market for methanol (por)", "methanol")]


def mdi_policy():
    regionalized = [
        {"name": name, "reference product": product}
        for name, product in (("market for aniline", "aniline"), ("aniline production", "aniline"),
                              ("market for nitrobenzene", "nitrobenzene"), ("nitrobenzene production", "nitrobenzene"))
    ]
    return PorLinkingPolicy({
        "regionalize": {"datasets": regionalized},
        "production pathways": {"FE_MDI": {"ecoinvent alias": {"name": "MDI production", "reference product": "MDI"}}},
        "markets": [{"name": "market for benzene (por)", "reference product": "benzene", "unit": "kilogram",
                     "includes": [], "replaces": [{"name": "market for benzene", "product": "benzene"}],
                     "replaces in": [{"location": "NL"}]}],
    })


def mdi_database():
    return [
        activity("MDI production", "MDI", exchanges=[feed("market for aniline", "aniline", "RER", 0.8)]),
        activity("market for aniline", "aniline", exchanges=[feed("aniline production", "aniline", "RoW", 1)]),
        activity("aniline production", "aniline", exchanges=[feed("market for nitrobenzene", "nitrobenzene", "RoW", 1.3)]),
        activity("market for nitrobenzene", "nitrobenzene", exchanges=[feed("nitrobenzene production", "nitrobenzene", "RoW", 1)]),
        activity("nitrobenzene production", "nitrobenzene", exchanges=[feed("market for benzene", "benzene", "RER", 0.6)]),
        activity("market for aniline", "aniline", "RER"),
        activity("aniline production", "aniline", "RoW"),
        activity("market for nitrobenzene", "nitrobenzene", "RoW"),
        activity("nitrobenzene production", "nitrobenzene", "RoW"),
        activity("market for benzene", "benzene", "RER"),
        activity("market for benzene (por)", "benzene"),
    ]


class EmbeddedMarketLinkingTest(unittest.TestCase):
    def test_full_mdi_chain_uses_declared_nl_production_and_por_benzene(self):
        database = mdi_database()
        before = deepcopy(database)
        result, audit, summary = link_por_database(database, mdi_policy())
        self.assertEqual(database, before)
        for position in range(5):
            self.assertEqual(result[position]["exchanges"][0]["location"], "NL")
            self.assertEqual(result[position]["exchanges"][0]["amount"], before[position]["exchanges"][0]["amount"])
        self.assertEqual(result[4]["exchanges"][0]["name"], "market for benzene (por)")
        self.assertEqual(sum(row["reason"] == "regionalized_market_product_supply" for row in audit), 2)
        self.assertEqual(summary["unavailable_local_suppliers"], 0)
        self.assertEqual(result[5:], before[5:])  # Foreign/background counterparts untouched.
        again, _, repeated = link_por_database(result, mdi_policy())
        self.assertEqual(again, result)
        self.assertEqual(repeated["changed_exchanges"], 0)

    def test_market_transport_biosphere_production_and_uncertainty_preserved(self):
        database = mdi_database()
        product = database[3]["exchanges"][0]
        product.update({"input": ("old", "old"), "database": "old", "code": "old",
                        "uncertainty type": 3, "loc": 1, "scale": 0.02, "minimum": 0, "maximum": None})
        transport = feed("market for transport, freight, lorry", "transport, freight, lorry", "World", 0.1713, "ton kilometer")
        database[3]["exchanges"].extend([transport, {"type": "biosphere", "amount": 0.001},
                                        {"type": "production", "amount": 1, "location": "NL"}])
        database.append(activity(transport["name"], transport["product"], "World", unit="ton kilometer"))
        before = deepcopy(database)
        result, _, _ = link_por_database(database, mdi_policy())
        linked = result[3]["exchanges"][0]
        for field in ("amount", "unit", "uncertainty type", "loc", "scale", "minimum", "maximum"):
            self.assertEqual(linked[field], product[field])
        for field in ("input", "database", "code"):
            self.assertNotIn(field, linked)
        self.assertEqual(result[3]["exchanges"][1:], before[3]["exchanges"][1:])

    def test_each_technology_share_kept_without_market_rebalancing(self):
        first, second = "nitrobenzene production", "nitrobenzene production, alternative"
        config = mdi_policy().config
        config["regionalize"]["datasets"].append({"name": second, "reference product": "nitrobenzene"})
        database = mdi_database()
        database[3]["exchanges"] = [feed(first, "nitrobenzene", "RoW", 0.4),
                                     feed(second, "nitrobenzene", "RER", 0.6)]
        database += [activity(second, "nitrobenzene", loc) for loc in ("RER", "NL")]
        result, _, _ = link_por_database(database, PorLinkingPolicy(config))
        self.assertEqual([e["amount"] for e in result[3]["exchanges"]], [0.4, 0.6])
        self.assertEqual([e["location"] for e in result[3]["exchanges"]], ["NL", "NL"])

    def test_explicit_foreign_country_supply_is_not_forced_local(self):
        database = mdi_database()
        database[3]["exchanges"][0]["location"] = "DE"
        database.append(activity("nitrobenzene production", "nitrobenzene", "DE"))
        result, _, _ = link_por_database(database, mdi_policy())
        self.assertEqual(result[3]["exchanges"][0]["location"], "DE")

    def test_unconfigured_and_import_producers_not_redirected(self):
        database = mdi_database()
        database[3]["exchanges"][0]["name"] = "nitrobenzene production, foreign import"
        database += [activity("nitrobenzene production, foreign import", "nitrobenzene", loc) for loc in ("RoW", "NL")]
        result, _, _ = link_por_database(database, mdi_policy())
        self.assertEqual(result[3]["exchanges"][0]["location"], "RoW")
        config = mdi_policy().config
        config["production pathways"]["nitrobenzene_terminal_import"] = {
            "production volume": {"variable": "Trade|To PoR|nitrobenzene"},
            "ecoinvent alias": {"name": "nitrobenzene production, foreign import", "reference product": "nitrobenzene"}}
        result, _, _ = link_por_database(database, PorLinkingPolicy(config))
        self.assertEqual(result[3]["exchanges"][0]["location"], "RoW")

    def test_missing_local_provider_reported_and_duplicate_local_provider_fails(self):
        database = mdi_database()
        del database[4]  # NL nitrobenzene production absent, valid RoW route remains.
        result, audit, summary = link_por_database(database, mdi_policy())
        self.assertEqual(result[3]["exchanges"][0]["location"], "RoW")
        self.assertEqual(summary["unavailable_local_suppliers"], 1)
        self.assertTrue(any(row["status"] == "unavailable_local_supplier" for row in audit))
        duplicate = mdi_database() + [deepcopy(mdi_database()[4])]
        with self.assertRaisesRegex(SupplierLinkingError, "Ambiguous"):
            link_por_database(duplicate, mdi_policy())

    def test_constructed_por_markets_keep_their_scenario_supply_routes(self):
        database = mdi_database()
        custom = database[-1]
        custom["exchanges"] = [feed("nitrobenzene production", "nitrobenzene", "RoW", 0.2)]
        result, _, _ = link_por_database(database, mdi_policy())
        self.assertEqual(result[-1]["exchanges"], custom["exchanges"])


class RegionalInputLinkingTest(unittest.TestCase):
    def run_process(self, exchange, providers, model="remind"):
        process = activity("nitrobenzene production", "nitrobenzene", exchanges=[exchange])
        return link_por_database([process, *providers], mdi_policy(), model=model)

    def test_valid_row_market_is_replaced_by_a_covering_european_market(self):
        for name, product, region in (
            ("market for nitric acid", "nitric acid", "RER w/o RU"),
            ("market for sulfuric acid", "sulfuric acid", "RER"),
            ("market for water, deionised", "water, deionised", "Europe without Switzerland"),
        ):
            with self.subTest(name=name):
                exchange = feed(name, product, "RoW", 0.5)
                result, audit, _ = self.run_process(exchange, [activity(name, product, loc) for loc in ("RoW", region)])
                self.assertEqual(result[0]["exchanges"][0]["location"], region)
                self.assertEqual(audit[0]["reason"], "geographic_covering_regional_supplier")
                self.assertEqual(result[0]["exchanges"][0]["amount"], 0.5)

    def test_world_heat_prefers_active_iam_region_for_both_models(self):
        name, product = "market for heat, district or industrial", "heat, district or industrial"
        for model, region in (("remind", "EUR"), ("image", "WEU")):
            with self.subTest(model=model):
                result, _, _ = self.run_process(feed(name, product, "World", 0.825, "megajoule"),
                    [activity(name, product, loc, unit="megajoule") for loc in ("World", region)], model)
                self.assertEqual(result[0]["exchanges"][0]["location"], region)

    def test_electricity_group_uses_real_nl_market_at_same_voltage_and_unit(self):
        group = "market group for electricity, medium voltage"
        market = "market for electricity, medium voltage"
        product = "electricity, medium voltage"
        providers = [activity(group, product, "GLO", unit="kilowatt hour"),
                     activity(group, product, "EUR", unit="kilowatt hour"),
                     activity(market, product, "NL", unit="kilowatt hour")]
        result, _, _ = self.run_process(feed(group, product, "GLO", 0.05, "kilowatt hour"), providers)
        self.assertEqual(exchange_key(result[0]["exchanges"][0]), (market, product, "kilowatt hour", "NL"))
        # Also handle an already-local group name whose exact NL provider is absent.
        result, _, _ = self.run_process(feed(group, product, "NL", 0.05, "kilowatt hour"), providers)
        self.assertEqual(result[0]["exchanges"][0]["name"], market)

    def test_unconfigured_generic_capital_proxy_can_use_rer_without_new_inventory(self):
        name, product = "chemical factory construction, organics", "chemical factory, organics"
        result, _, _ = self.run_process(feed(name, product, "RoW", 4e-10, "unit"),
            [activity(name, product, loc, unit="unit") for loc in ("RoW", "RER")])
        self.assertEqual(result[0]["exchanges"][0]["location"], "RER")
        self.assertEqual(result[0]["exchanges"][0]["amount"], 4e-10)

    def test_operational_electricity_uses_nl_not_source_inventory_country(self):
        name, product = "market for electricity, medium voltage", "electricity, medium voltage"
        result, _, _ = self.run_process(feed(name, product, "DE", 0.05, "kilowatt hour"),
            [activity(name, product, loc, unit="kilowatt hour") for loc in ("DE", "NL")])
        self.assertEqual(result[0]["exchanges"][0]["location"], "NL")

    def test_import_wrapper_keeps_original_foreign_energy_market(self):
        name, product = "market for electricity, medium voltage", "electricity, medium voltage"
        source = feed(name, product, "MA", 0.5, "kilowatt hour")
        imported = activity("methanol, imported", "methanol", exchanges=[source])
        p = policy(imports=True, profiles={dataset_key(imported): [deepcopy(source)]})
        providers = [activity(name, product, loc, unit="kilowatt hour") for loc in ("MA", "NL", "EUR")]
        result, _, _ = link_por_database([imported, *providers], p)
        self.assertEqual(result[0]["exchanges"][0]["location"], "MA")

    def test_negative_wastewater_keeps_sign_and_uncertainty(self):
        name, product = "market for wastewater, average", "wastewater, average"
        exchange = feed(name, product, "RoW", -0.00034633, "cubic meter",
                        **{"uncertainty type": 3, "loc": -0.00034633, "scale": 1e-5, "minimum": None, "maximum": 0})
        result, _, _ = self.run_process(exchange,
            [activity(name, product, loc, unit="cubic meter") for loc in ("RoW", "Europe without Switzerland")])
        linked = result[0]["exchanges"][0]
        self.assertEqual(linked["location"], "Europe without Switzerland")
        for field in ("amount", "unit", "uncertainty type", "loc", "scale", "minimum", "maximum"):
            self.assertEqual(linked[field], exchange[field])

    def test_foreign_country_market_and_foreign_process_remain_pinned(self):
        for name in ("market for nitric acid", "nitric acid production"):
            with self.subTest(name=name):
                result, _, _ = self.run_process(feed(name, "nitric acid", "CN"),
                    [activity(name, "nitric acid", loc) for loc in ("CN", "RER", "NL")])
                self.assertEqual(result[0]["exchanges"][0]["location"], "CN")

    def test_no_covering_provider_keeps_global_proxy_with_audit(self):
        name, product = "market for nitric acid", "nitric acid"
        result, audit, summary = self.run_process(feed(name, product, "RoW"),
            [activity(name, product, loc) for loc in ("RoW", "CN")])
        self.assertEqual(result[0]["exchanges"][0]["location"], "RoW")
        self.assertEqual(summary["retained_geographic_proxies"], 1)
        self.assertTrue(any(row["status"] == "retained_geographic_proxy" for row in audit))

    def test_product_and_unit_mismatches_do_not_become_regional_substitutes(self):
        name, product = "market for nitric acid", "nitric acid"
        providers = [activity(name, product, "RoW"), activity(name, "sulfuric acid", "RER"),
                     activity(name, product, "NL", unit="megajoule")]
        result, _, _ = self.run_process(feed(name, product, "RoW"), providers)
        self.assertEqual(result[0]["exchanges"][0]["location"], "RoW")

    def test_regional_market_ancillary_transport_changes_identity_not_distance(self):
        name, product = "market for transport, freight, lorry", "transport, freight, lorry"
        database = mdi_database()
        database[3]["exchanges"].append(feed(name, product, "World", 0.1713, "ton kilometer"))
        database += [activity(name, product, loc, unit="ton kilometer") for loc in ("World", "EUR")]
        result, _, _ = link_por_database(database, mdi_policy())
        self.assertEqual(result[3]["exchanges"][1]["location"], "EUR")
        self.assertEqual(result[3]["exchanges"][1]["amount"], 0.1713)

    def test_duplicate_regional_provider_fails_instead_of_picking_one(self):
        name, product = "market for nitric acid", "nitric acid"
        duplicate = activity(name, product, "RER")
        with self.assertRaisesRegex(SupplierLinkingError, "Ambiguous"):
            self.run_process(feed(name, product, "RoW"), [activity(name, product, "RoW"), duplicate, deepcopy(duplicate)])

    def test_qualified_regional_candidate_keeps_biosphere_and_is_idempotent(self):
        name, product = "market for sulfuric acid", "sulfuric acid"
        process = activity("nitrobenzene production", "nitrobenzene", exchanges=[feed(name, product, "RoW")])
        bio = {"name": "Carbon dioxide, fossil", "type": "biosphere", "amount": 0.09381614}
        production = {"name": process["name"], "type": "production", "location": "NL", "amount": 1}
        process["exchanges"].extend([bio, production])
        database = [process, activity(name, product, "RoW"), activity(name, product, "RER")]
        before = deepcopy(database)
        result, _, _ = link_por_database(database, mdi_policy())
        self.assertEqual(database, before)
        self.assertEqual(result[0]["exchanges"][1:], [bio, production])
        repeated, _, summary = link_por_database(result, mdi_policy())
        self.assertEqual(repeated, result)
        self.assertEqual(summary["changed_exchanges"], 0)


class LinkingTest(unittest.TestCase):
    def test_explicit_renaming_preserves_quantities_uncertainty_and_other_flows(self):
        database = basic_database()
        database[0]["exchanges"][0].update({"uncertainty type": 3, "loc": 2, "scale": 0.1,
                                            "minimum": 0, "maximum": 4, "input": ("old", "old_code")})
        bio = {"name": "Carbon dioxide, fossil", "amount": 0.4, "type": "biosphere"}
        production = {"name": "PPP production", "amount": 1, "type": "production"}
        database[0]["exchanges"].extend([bio, production])
        original = deepcopy(database)
        result, audit, summary = link_por_database(database, policy())
        self.assertEqual(database, original)
        linked = result[0]["exchanges"][0]
        self.assertEqual(linked["name"], "market for methanol (por)")
        self.assertEqual(linked["location"], "NL")
        self.assertNotIn("input", linked)
        for field in ("amount", "unit", "uncertainty type", "loc", "scale", "minimum", "maximum"):
            self.assertEqual(linked[field], original[0]["exchanges"][0][field])
        self.assertIs(result[0]["exchanges"][1], bio)
        self.assertIs(result[0]["exchanges"][2], production)
        self.assertEqual(summary["changed_exchanges"], 1)
        self.assertEqual(audit[0]["reason"], "configured_por_market")
        again, _, repeat = link_por_database(result, policy())
        self.assertEqual(again, result)
        self.assertEqual(repeat["changed_exchanges"], 0)

    def test_forecast_market_is_replaced_explicitly(self):
        database = basic_database()
        database[0]["exchanges"][0]["name"] = "market for methanol (SPS)"
        result, _, _ = link_por_database(database, policy())
        self.assertEqual(result[0]["exchanges"][0]["name"], "market for methanol (por)")

    def test_native_import_redirection_is_restored_and_market_share_unchanged(self):
        imported = activity("methanol, imported", "methanol", exchanges=[feed("market for methanol (por)", location="NL", amount=1)])
        profile = {dataset_key(imported): [feed(amount=1)]}
        database = basic_database() + [imported]
        database[2]["exchanges"] = [feed("methanol, imported", location="NL", amount=0.35)]
        result, audit, _ = link_por_database(database, policy(True, profile))
        self.assertEqual(exchange_key(result[3]["exchanges"][0]), ("market for methanol", "methanol", "kilogram", "RER"))
        self.assertEqual(result[2]["exchanges"], database[2]["exchanges"])
        self.assertTrue(any(row["reason"] == "restored_protected_source_supplier" for row in audit))

    def test_regionalized_import_copy_uses_original_foreign_recipe(self):
        imported = activity("methanol, imported", "methanol", exchanges=[feed("market for methanol (por)", location="NL", amount=1)])
        original_key = ("methanol, imported", "methanol", "kilogram", "RER")
        profile = {original_key: [feed(amount=1)]}
        result, _, _ = link_por_database(basic_database() + [imported], policy(True, profile))
        self.assertEqual(result[-1]["exchanges"][0]["location"], "RER")

    def test_import_preserves_foreign_process_even_when_nl_copy_exists(self):
        imported = activity("methanol, imported", "methanol", exchanges=[feed("methanol synthesis for por", "methanol, unpurified", "MA", 1)])
        profile = {dataset_key(imported): deepcopy(imported["exchanges"])}
        sources = [activity("methanol synthesis for por", "methanol, unpurified", location) for location in ("MA", "NL")]
        result, _, summary = link_por_database(basic_database() + [imported] + sources, policy(True, profile))
        self.assertEqual(result[3]["exchanges"][0]["location"], "MA")
        self.assertEqual(summary["changed_exchanges"], 1)  # Domestic PPP only.

    def test_fixed_solar_wind_suppliers_and_shares_are_pinned(self):
        name = "electricity supply, 50% solar and 50% wind, low voltage, for DAC methanol (por)"
        source = [feed("solar PV", "electricity, low voltage", "MA", 0.5, "kilowatt hour"),
                  feed("onshore wind", "electricity, high voltage", "RoW", 0.5, "kilowatt hour")]
        mix = activity(name, "electricity, low voltage", "MA", deepcopy(source), "kilowatt hour")
        mix["exchanges"][0]["location"] = "NL"  # Simulate undesired geographical relinking.
        profiles = {dataset_key(mix): source}
        providers = [activity("solar PV", "electricity, low voltage", loc, unit="kilowatt hour") for loc in ("MA", "NL")]
        providers += [activity("onshore wind", "electricity, high voltage", "RoW", unit="kilowatt hour")]
        result, _, _ = link_por_database([mix] + providers, policy(profiles=profiles))
        self.assertEqual(result[0]["exchanges"][0]["location"], "MA")
        self.assertEqual([e["amount"] for e in result[0]["exchanges"]], [0.5, 0.5])
        mix["exchanges"][0]["amount"] = 0.6
        with self.assertRaisesRegex(SupplierLinkingError, "share changed"):
            link_por_database([mix] + providers, policy(profiles=profiles))

    def test_generic_local_market_and_missing_regional_market_fallback(self):
        electricity = feed("market for electricity, medium voltage", "electricity, medium voltage", "RER", 3, "kilowatt hour")
        consumer = activity("PPP production", "polypropylene", exchanges=[electricity])
        nl = activity(electricity["name"], electricity["product"], "NL", unit="kilowatt hour")
        old = activity(electricity["name"], electricity["product"], "RER", unit="kilowatt hour")
        result, _, _ = link_por_database([consumer, old, nl], policy())
        self.assertEqual(result[0]["exchanges"][0]["location"], "NL")
        eur = activity(electricity["name"], electricity["product"], "EUR", unit="kilowatt hour")
        result, _, _ = link_por_database([consumer, eur], policy())
        self.assertEqual(result[0]["exchanges"][0]["location"], "EUR")

    def test_absent_inactive_market_keeps_valid_supplier_with_audit(self):
        result, audit, summary = link_por_database(basic_database()[:2], policy())
        self.assertEqual(result[0]["exchanges"][0]["location"], "RER")
        self.assertEqual(summary["changed_exchanges"], 0)
        self.assertTrue(any(row["status"] == "unavailable_market" for row in audit))

    def test_ambiguous_unlinked_and_self_supply_fail_without_mutation(self):
        duplicate = basic_database() + [deepcopy(basic_database()[2])]
        with self.assertRaisesRegex(SupplierLinkingError, "Ambiguous"):
            link_por_database(duplicate, policy())
        with self.assertRaisesRegex(SupplierLinkingError, "Unlinked"):
            link_por_database(basic_database()[:1], policy())
        self_feed = activity("PPP production", "polypropylene", exchanges=[feed("PPP production", "polypropylene", "NL")])
        with self.assertRaisesRegex(SupplierLinkingError, "self-supply"):
            link_por_database([self_feed], policy())

    def test_source_profile_product_can_be_resolved_without_guessing(self):
        imported = activity("methanol, imported", "methanol", exchanges=[feed("market for methanol (por)", location="NL", amount=1)])
        original = feed(amount=1)
        original["product"] = None
        profile = {dataset_key(imported): [original]}
        result, _, _ = link_por_database(basic_database() + [imported], policy(True, profile))
        self.assertEqual(result[-1]["exchanges"][0]["product"], "methanol")


class ExistingElectricityLossTest(unittest.TestCase):
    """Inherited grid-loss inputs are not newly created circular suppliers."""

    def loss_case(self, *, name="market for electricity, medium voltage",
                  product="electricity, medium voltage", amount=0.00275,
                  unit="kilowatt hour", extra=None, production=None):
        exchange = feed(name, product, "NL", amount, unit, **(extra or {}))
        consumer = activity(name, product, exchanges=[exchange], unit=unit)
        consumer["database"] = "scenario"
        if production is not None:
            consumer["exchanges"].append(production)
        selected = PorLinkingPolicy({"regionalize": {"datasets": [
            {"name": name, "reference product": product}]}, "markets": []})
        return consumer, exchange, selected

    def assert_preserved_loss(self, consumer, exchange, selected):
        before = deepcopy(consumer)
        result, audit, summary = link_por_database([consumer], selected)
        self.assertEqual(consumer, before)
        self.assertEqual(result[0], before)
        self.assertIs(result[0]["exchanges"][0], exchange)
        self.assertEqual(summary["changed_exchanges"], 0)
        self.assertEqual(summary["preserved_electricity_loss_exchanges"], 1)
        preserved = [row for row in audit if row["status"] == "preserved_electricity_loss"]
        self.assertEqual(len(preserved), 1)
        self.assertEqual(preserved[0]["reason"], "preserved_inherited_electricity_loss")
        self.assertEqual(preserved[0]["amount_before"], exchange["amount"])
        self.assertEqual(preserved[0]["amount_after"], exchange["amount"])
        again, repeated_audit, repeated_summary = link_por_database(result, selected)
        self.assertEqual(again, result)
        self.assertEqual(repeated_audit, audit)
        self.assertEqual(repeated_summary["changed_exchanges"], 0)
        self.assertEqual(repeated_summary["preserved_electricity_loss_exchanges"], 1)
        return result

    def test_standard_markets_and_groups_keep_nonhardcoded_loss_ratios(self):
        for prefix in ("market for", "market group for"):
            for voltage in ("high", "medium", "low"):
                for ratio in (0.000125, 0.00275, 0.031, 0.12):
                    with self.subTest(prefix=prefix, voltage=voltage, ratio=ratio):
                        consumer, exchange, selected = self.loss_case(
                            name=f"{prefix} electricity, {voltage} voltage",
                            product=f"electricity, {voltage} voltage", amount=ratio)
                        self.assert_preserved_loss(consumer, exchange, selected)

    def test_native_period_market_keeps_input_address_and_all_metadata(self):
        consumer, exchange, selected = self.loss_case(
            name="market group for electricity, medium voltage, 10-year period",
            extra={"uncertainty type": 3, "loc": 0.00275, "scale": 0.00015,
                   "minimum": None, "maximum": 0.1, "negative": False,
                   "comment": "Inherited transformation losses", "pedigree": {"reliability": 2}})
        exchange["input"] = (consumer["database"], consumer["code"])
        exchange["database"] = consumer["database"]
        exchange["code"] = consumer["code"]
        self.assert_preserved_loss(consumer, exchange, selected)

    def test_native_self_address_does_not_require_consumer_database_metadata(self):
        consumer, exchange, selected = self.loss_case()
        consumer.pop("database")
        exchange["input"] = ("scenario", consumer["code"])
        self.assert_preserved_loss(consumer, exchange, selected)

    def test_fraction_is_relative_to_explicit_reference_production(self):
        for output, loss in ((0.1, 0.00275), (2.0, 1.2)):
            with self.subTest(output=output, loss=loss):
                production = {"type": "production", "amount": output,
                              "name": "market for electricity, medium voltage",
                              "product": "electricity, medium voltage",
                              "unit": "kilowatt hour", "location": "NL"}
                consumer, exchange, selected = self.loss_case(amount=loss, production=production)
                result = self.assert_preserved_loss(consumer, exchange, selected)
                self.assertIs(result[0]["exchanges"][1], production)

    def test_invalid_or_nonfractional_self_input_is_not_a_loss_exception(self):
        for amount in (-0.00275, 1.0, 1.5, float("nan"), float("inf"), -float("inf"), True):
            with self.subTest(amount=amount):
                consumer, _, selected = self.loss_case(amount=amount)
                with self.assertRaises(SupplierLinkingError):
                    link_por_database([consumer], selected)

    def test_invalid_or_missing_matching_reference_output_is_rejected(self):
        for production in (
            {"type": "production", "amount": 0},
            {"type": "production", "amount": -1},
            {"type": "production", "amount": float("inf")},
            {"type": "production", "amount": 0.001},
            {"type": "production", "amount": 1, "product": "heat", "unit": "megajoule"},
        ):
            with self.subTest(production=production):
                consumer, _, selected = self.loss_case(production=production)
                with self.assertRaises(SupplierLinkingError):
                    link_por_database([consumer], selected)

    def test_multiple_loss_exchanges_are_preserved_only_below_reference_output(self):
        consumer, first, selected = self.loss_case(amount=0.2)
        second = feed(first["name"], first["product"], "NL", 0.3, "kilowatt hour")
        consumer["exchanges"].append(second)
        result, audit, summary = link_por_database([consumer], selected)
        self.assertIs(result[0]["exchanges"][0], first)
        self.assertIs(result[0]["exchanges"][1], second)
        self.assertEqual(summary["changed_exchanges"], 0)
        self.assertEqual(summary["preserved_electricity_loss_exchanges"], 2)
        self.assertEqual(sum(row["status"] == "preserved_electricity_loss" for row in audit), 2)
        for exchange in consumer["exchanges"]:
            exchange["amount"] = 0.6
        with self.assertRaisesRegex(SupplierLinkingError, "self-supply"):
            link_por_database([consumer], selected)

    def test_explicit_supplier_code_and_database_cannot_disagree_with_consumer(self):
        for metadata in ({"code": "foreign_code"}, {"database": "foreign_database"}):
            with self.subTest(metadata=metadata):
                consumer, _, selected = self.loss_case(extra=metadata)
                with self.assertRaisesRegex(SupplierLinkingError, "self-supply"):
                    link_por_database([consumer], selected)

    def test_self_pointer_must_not_be_stale_foreign_or_malformed(self):
        for pointer in (
            ("scenario", "another_code"), ("another_database", "market for electricity, medium voltage_NL"),
            "scenario:code", (), ("scenario",), ("scenario", "code", "unexpected"),
            (None, "market for electricity, medium voltage_NL"),
        ):
            with self.subTest(pointer=pointer):
                consumer, _, selected = self.loss_case(extra={"input": pointer})
                with self.assertRaisesRegex(SupplierLinkingError, "self-supply"):
                    link_por_database([consumer], selected)

    def test_noncanonical_electricity_or_non_electricity_market_is_rejected(self):
        for name, product, unit in (
            ("market for hydrogen", "hydrogen", "kilogram"),
            ("market for electricity, medium voltage (por)", "electricity, medium voltage", "kilowatt hour"),
            ("electricity supply, 50% wind", "electricity, medium voltage", "kilowatt hour"),
            ("market for electricity, medium voltage", "electricity, low voltage", "kilowatt hour"),
            ("market for electricity, medium voltage", "electricity, medium voltage", "megajoule"),
        ):
            with self.subTest(name=name, product=product, unit=unit):
                consumer, _, selected = self.loss_case(name=name, product=product, unit=unit)
                with self.assertRaisesRegex(SupplierLinkingError, "self-supply"):
                    link_por_database([consumer], selected)

    def test_constructed_market_does_not_receive_native_loss_exemption(self):
        consumer, _, _ = self.loss_case()
        selected = PorLinkingPolicy({"markets": [
            {"name": consumer["name"], "reference product": consumer["reference product"],
             "unit": consumer["unit"], "includes": []}]})
        with self.assertRaisesRegex(SupplierLinkingError, "self-supply"):
            link_por_database([consumer], selected)

    def test_configured_replacement_cannot_introduce_new_electricity_self_input(self):
        consumer, _, _ = self.loss_case()
        foreign = feed("market for electricity, medium voltage, legacy", "electricity, medium voltage",
                       "RER", 0.00275, "kilowatt hour")
        consumer["exchanges"] = [foreign]
        provider = activity(foreign["name"], foreign["product"], "RER", unit=foreign["unit"])
        selected = PorLinkingPolicy({"markets": [
            {"name": consumer["name"], "reference product": consumer["reference product"],
             "unit": consumer["unit"], "includes": [],
             "add": [{"name": foreign["name"], "reference product": foreign["product"]}],
             "replaces": [{"name": foreign["name"], "product": foreign["product"]}],
             "replaces in": [{"location": "NL"}]}]})
        before = deepcopy([consumer, provider])
        with self.assertRaisesRegex(SupplierLinkingError, "self-supply"):
            link_por_database([consumer, provider], selected)
        self.assertEqual([consumer, provider], before)


class CacheAndHookTest(unittest.TestCase):
    def cache_adapter(self, directory):
        counter = [0]

        def load(scenario, original_database, delete, load_metadata, warning):
            self.assertFalse(delete)
            self.assertFalse(load_metadata)
            with Path(scenario.pop("database filepath")).open("rb") as stream:
                scenario["database"] = pickle.load(stream)
            return scenario

        def dump(scenario):
            counter[0] += 1
            destination = Path(directory) / f"new_{counter[0]}.pickle"
            with destination.open("wb") as stream:
                pickle.dump(scenario.pop("database"), stream)
            scenario["database filepath"] = destination
            return scenario

        return load, dump

    def scenario(self, path, label="CL"):
        return {"model": "remind", "pathway": "test", "year": 2050,
                "external scenarios": [{"scenario": label, "data": "datapackage_itom_por.json"}],
                "applied functions": ["external"], "database filepath": path}

    def test_both_cached_scenarios_get_the_embedded_market_fix(self):
        with tempfile.TemporaryDirectory(prefix="por_embedded_test_") as directory:
            scenarios = []
            for label in ("CL", "OCE"):
                path = Path(directory) / f"original_{label}.pickle"
                path.write_bytes(pickle.dumps(mdi_database()))
                scenarios.append(self.scenario(path, label))
            builder = SimpleNamespace(scenarios=scenarios, database=[])
            load, dump = self.cache_adapter(directory)
            reports = apply_por_supplier_linking(builder, policy=mdi_policy(), report_dir=Path(directory) / "reports",
                                                load_database_func=load, dump_database_func=dump, verbose=False)
            self.assertEqual(len(reports), 2)
            for scenario in scenarios:
                fixed = pickle.loads(Path(scenario["database filepath"]).read_bytes())
                self.assertEqual(fixed[3]["exchanges"][0]["location"], "NL")

    def test_cached_scenarios_are_saved_and_reported_without_deleting_originals(self):
        with tempfile.TemporaryDirectory(prefix="por_linking_test_") as directory:
            original = Path(directory) / "original.pickle"
            original.write_bytes(pickle.dumps(basic_database()))
            builder = SimpleNamespace(scenarios=[self.scenario(original)], database=[])
            load, dump = self.cache_adapter(directory)
            reports = apply_por_supplier_linking(builder, policy=policy(), report_dir=Path(directory) / "reports",
                                                load_database_func=load, dump_database_func=dump, verbose=False)
            self.assertTrue(original.exists())
            self.assertNotIn("database", builder.scenarios[0])
            changed = pickle.loads(Path(builder.scenarios[0]["database filepath"]).read_bytes())
            self.assertEqual(changed[0]["exchanges"][0]["name"], "market for methanol (por)")
            self.assertEqual(reports[0]["changed_exchanges"], 1)
            self.assertEqual(len(list((Path(directory) / "reports").glob("*.csv"))), 1)

    def test_installed_premise_cache_functions_round_trip_the_modified_database(self):
        package = find_spec("premise")
        if package is None:
            self.skipTest("Installed premise source is required")
        path = Path(next(iter(package.submodule_search_locations))) / "utils.py"
        parsed = ast.parse(path.read_text(encoding="utf-8"))
        functions = [node for node in parsed.body if isinstance(node, ast.FunctionDef)
                     and node.name in ("dump_database", "load_database", "get_uuids")]
        with tempfile.TemporaryDirectory(prefix="por_linking_test_") as directory:
            namespace = {"pickle": pickle, "uuid": uuid, "Path": Path, "Dict": Dict, "Any": Any,
                         "List": List, "Iterable": Iterable, "DIR_CACHED_FILES": Path(directory)}
            # New premise delegates legacy serialization to cache helpers. Keep
            # this test isolated from real caches/Brightway while executing the
            # installed load/dump functions for either supported API generation.
            def create_scenario_cache(database, path):
                Path(path).write_bytes(pickle.dumps(database))
                return Path(path), None
            namespace.update(create_scenario_cache=create_scenario_cache,
                             load_cached_database=lambda path: pickle.loads(Path(path).read_bytes()),
                             restore_cached_classifications=lambda *args: None)
            exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), "exec"), namespace)
            original = Path(directory) / "original.pickle"
            original.write_bytes(pickle.dumps(basic_database()))
            builder = SimpleNamespace(scenarios=[self.scenario(original)], database=[])
            apply_por_supplier_linking(builder, policy=policy(), report_dir=Path(directory) / "reports",
                                       load_database_func=namespace["load_database"],
                                       dump_database_func=namespace["dump_database"], verbose=False)
            self.assertTrue(original.exists())
            modified = pickle.loads(Path(builder.scenarios[0]["database filepath"]).read_bytes())
            self.assertEqual(modified[0]["exchanges"][0]["name"], "market for methanol (por)")

    def test_later_failure_does_not_commit_any_scenario_pointer(self):
        with tempfile.TemporaryDirectory(prefix="por_linking_test_") as directory:
            good, bad = Path(directory) / "good.pickle", Path(directory) / "bad.pickle"
            good.write_bytes(pickle.dumps(basic_database()))
            bad.write_bytes(pickle.dumps(basic_database()[:1]))
            builder = SimpleNamespace(scenarios=[self.scenario(good), self.scenario(bad, "OCE")], database=[])
            before = deepcopy(builder.scenarios)
            load, dump = self.cache_adapter(directory)
            with self.assertRaises(SupplierLinkingError):
                apply_por_supplier_linking(builder, policy=policy(), report_dir=Path(directory) / "reports",
                                           load_database_func=load, dump_database_func=dump, verbose=False)
            self.assertEqual(builder.scenarios, before)
            self.assertTrue(good.exists() and bad.exists())

    def test_missing_cache_never_falls_back_to_original_database(self):
        scenario = self.scenario(Path("unused"))
        scenario.pop("database filepath")
        builder = SimpleNamespace(scenarios=[scenario], database=[])
        with self.assertRaisesRegex(SupplierLinkingError, "refusing"):
            apply_por_supplier_linking(builder, policy=policy(), load_database_func=lambda **kwargs: None,
                                       dump_database_func=lambda **kwargs: None, verbose=False)

    def test_package_internal_update_runs_post_step_once_before_export(self):
        with tempfile.TemporaryDirectory(prefix="por_linking_test_") as directory:
            original = Path(directory) / "original.pickle"
            original.write_bytes(pickle.dumps(basic_database()))
            events = []
            builder = SimpleNamespace(scenarios=[self.scenario(original)], database=[])
            builder.update = lambda: events.append("update")
            load, dump = self.cache_adapter(directory)
            args = dict(policy=policy(), report_dir=Path(directory) / "reports", stage="pathways",
                        install_support=False, load_database_func=load, dump_database_func=dump, verbose=False)
            self.assertTrue(attach_por_supplier_linking(builder, **args))
            self.assertFalse(attach_por_supplier_linking(builder, **args))

            def create_package():
                builder.update()
                changed = pickle.loads(Path(builder.scenarios[0]["database filepath"]).read_bytes())
                self.assertEqual(changed[0]["exchanges"][0]["name"], "market for methanol (por)")
                events.append("export")

            create_package()
            self.assertEqual(events, ["update", "export"])
            self.assertEqual(len(builder.por_supplier_linking_reports), 1)


class LegacyHookTest(unittest.TestCase):
    def test_legacy_method_is_disabled_idempotently(self):
        class Legacy:
            def fully_regionalize_created_inventories(self, location="NL", contained=False):
                # Legacy body used get_recursively and relink_technosphere_exchanges_full.
                raise AssertionError("Legacy hook must not run")

        self.assertTrue(disable_legacy_por_regionalization(Legacy, verbose=False))
        Legacy().fully_regionalize_created_inventories()
        self.assertFalse(disable_legacy_por_regionalization(Legacy, verbose=False))
        self.assertFalse(disable_legacy_por_regionalization(type("Native", (), {}), verbose=False))


class NativeExportGuardTest(unittest.TestCase):
    def native_export_module(self):
        package = find_spec("premise")
        if package is None:
            raise unittest.SkipTest("Installed premise source is required")
        path = Path(next(iter(package.submodule_search_locations))) / "export.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == "check_geographical_linking")
        namespace = {}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
        return SimpleNamespace(check_geographical_linking=namespace[function.name])

    def scenario(self, marked):
        consumer = activity("PPP production", "polypropylene", exchanges=[feed("synthesis", "methanol", "MA", 1)])
        scenario = {"database": [consumer], "index": {("synthesis", "methanol"): [{"location": "NL"}]}}
        if marked:
            scenario["por supplier linking"] = {"policy_token": _register_policy(policy()),
                                                "linking_policy_version": LINKING_POLICY_VERSION}
        return scenario

    def test_actual_native_export_check_cannot_undo_validated_foreign_link(self):
        export = self.native_export_module()
        native_scenario = self.scenario(False)
        export.check_geographical_linking(native_scenario, [])
        self.assertEqual(native_scenario["database"][0]["exchanges"][0]["location"], "NL")
        self.assertTrue(install_por_export_geography_guard(export, verbose=False))
        self.assertFalse(install_por_export_geography_guard(export, verbose=False))
        scenario = self.scenario(True)
        export.check_geographical_linking(scenario, [])
        self.assertEqual(scenario["database"][0]["exchanges"][0]["location"], "MA")
        self.assertEqual(scenario["por supplier linking"]["prevented_export_geography_changes"], 1)

    def test_unmarked_scenarios_keep_native_export_behavior(self):
        export = self.native_export_module()
        install_por_export_geography_guard(export, verbose=False)
        scenario = self.scenario(False)
        export.check_geographical_linking(scenario, [])
        self.assertEqual(scenario["database"][0]["exchanges"][0]["location"], "NL")

    def test_embedded_market_correction_survives_a_later_native_change(self):
        result, _, _ = link_por_database(mdi_database(), mdi_policy())
        def change_market_supplier(scenario, original_database):
            scenario["database"][3]["exchanges"][0]["location"] = "RoW"
            return scenario
        export = SimpleNamespace(check_geographical_linking=change_market_supplier)
        install_por_export_geography_guard(export, verbose=False)
        scenario = {"database": result, "por supplier linking": {"policy_token": _register_policy(mdi_policy()),
                                                                  "linking_policy_version": LINKING_POLICY_VERSION}}
        export.check_geographical_linking(scenario, [])
        self.assertEqual(scenario["database"][3]["exchanges"][0]["location"], "NL")
        self.assertEqual(scenario["por supplier linking"]["prevented_export_geography_changes"], 1)

    def test_export_rejects_tags_from_the_old_linking_rules(self):
        export = self.native_export_module()
        install_por_export_geography_guard(export, verbose=False)
        scenario = self.scenario(True)
        del scenario["por supplier linking"]["linking_policy_version"]
        before = deepcopy(scenario["database"])
        with self.assertRaisesRegex(SupplierLinkingError, "outdated supplier-linking"):
            export.check_geographical_linking(scenario, [])
        self.assertEqual(scenario["database"], before)

        scenario = self.scenario(True)
        scenario["por supplier linking"]["linking_policy_version"] = 2
        with self.assertRaisesRegex(SupplierLinkingError, "outdated supplier-linking"):
            export.check_geographical_linking(scenario, [])


if __name__ == "__main__":
    unittest.main()
