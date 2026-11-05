import unittest

from extract_repo_skills.roadmap_extractor import build_hierarchy


def node(node_id, kind, label, x, y, width=150, height=40, font_size=17):
    return {
        "id": node_id,
        "type": kind,
        "position": {"x": x, "y": y},
        "measured": {"width": width, "height": height},
        "data": {"label": label, "style": {"fontSize": font_size}},
    }


def edge(source, target, style="dashed"):
    return {
        "source": source,
        "target": target,
        "data": {"edgeStyle": style},
    }


def document() -> dict:
    return {
        "nodes": [
            node("title", "title", "Example", 0, -400),
            node("intro", "topic", "Introduction", -314, 0),
            node("routing", "topic", "Routing", -314, 500),
            node("observability", "topic", "Observability", -314, 1000),
            node("packet", "topic", "Packet Analysis", -314, 800),
            node("intro-section", "section", "", -710, -50, 300, 300),
            node("basic", "label", "Basic Terminology", -638, -20),
            node("client", "subtopic", "Client", -691, 0),
            node("server", "subtopic", "Server", -691, 50),
            node("routing-label", "label", "Routing Protocols", -711, 450),
            node("ospf", "subtopic", "OSPF", -707, 480),
            node("bgp", "subtopic", "BGP", -707, 530),
            node("obs-label", "label", "Network Observability Tools", 86, 950),
            node("wireshark", "subtopic", "Wireshark", 66, 980),
            node("wireless-security", "subtopic", "Wireless Security", -710, 820),
            node("wpa", "subtopic", "WPA vs WPS", -710, 870),
        ],
        "edges": [
            edge("title", "intro", "solid"),
            edge("intro", "routing", "solid"),
            edge("routing", "packet", "solid"),
            edge("packet", "observability", "solid"),
            edge("intro", "intro-section"),
            edge("wireless-security", "wpa"),
        ],
    }


class RoadmapExtractorTests(unittest.TestCase):
    def test_uses_sections_labels_and_dashed_edges(self):
        tree = build_hierarchy(document())
        by_name = {topic["name"]: topic for topic in tree}

        intro = by_name["Introduction"]["subtopics"]
        self.assertEqual([item["name"] for item in intro], ["Basic Terminology"])
        self.assertEqual(
            [item["name"] for item in intro[0]["subtopics"]],
            ["Client", "Server"],
        )

        routing = by_name["Routing"]["subtopics"]
        self.assertEqual([item["name"] for item in routing], ["Routing Protocols"])
        self.assertEqual(
            [item["name"] for item in routing[0]["subtopics"]],
            ["OSPF", "BGP"],
        )

        observability = by_name["Observability"]["subtopics"]
        self.assertEqual(
            [item["name"] for item in observability],
            ["Network Observability Tools"],
        )
        self.assertEqual(
            [item["name"] for item in observability[0]["subtopics"]],
            ["Wireshark"],
        )

        packet = by_name["Packet Analysis"]["subtopics"]
        self.assertEqual([item["name"] for item in packet], ["Wireless Security"])
        self.assertEqual(
            [item["name"] for item in packet[0]["subtopics"]],
            ["WPA vs WPS"],
        )

    def test_groups_are_marked_as_group_nodes(self):
        tree = build_hierarchy(document())
        intro = next(topic for topic in tree if topic["name"] == "Introduction")
        self.assertEqual(intro["subtopics"][0]["type"], "group")


if __name__ == "__main__":
    unittest.main()
