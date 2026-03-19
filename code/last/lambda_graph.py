import json
import boto3
import networkx as nx
from decimal import Decimal

GRAPH_TABLE = "rag-graph"
REGION = "us-east-1"

dynamodb = boto3.resource("dynamodb", region_name=REGION)
graph_table = dynamodb.Table(GRAPH_TABLE)

def load_graph():
    resp = graph_table.scan()
    items = resp.get("Items", [])
    G = nx.DiGraph()
    for item in items:
        node_id = item["node_id"]
        label = item.get("label", node_id)
        G.add_node(node_id, label=label, node_type=item.get("node_type", "entity"))
        for edge in item.get("edges", []):
            G.add_edge(node_id, edge["target"], relation=edge.get("relation", "related_to"))
    return G

def query_related(node_id, depth=2):
    G = load_graph()
    if node_id not in G:
        return []
    related = []
    for target in nx.descendants(G, node_id):
        path_length = nx.shortest_path_length(G, node_id, target)
        if path_length <= depth:
            related.append({"node_id": target, "label": G.nodes[target].get("label", target), "depth": path_length})
    return sorted(related, key=lambda x: x["depth"])

def add_node(node_id, label, node_type, edges, source_chunk_id):
    graph_table.put_item(Item={"node_id": node_id, "label": label, "node_type": node_type, "edges": edges, "source_chunk_id": source_chunk_id})

def lambda_handler(event, context):
    action = event.get("action", "query")
    if action == "query":
        node_id = event.get("node_id", "")
        depth = int(event.get("depth", 2))
        results = query_related(node_id, depth)
        return {"statusCode": 200, "body": json.dumps({"related": results})}
    if action == "add_node":
        add_node(event["node_id"], event["label"], event.get("node_type", "entity"), event.get("edges", []), event.get("source_chunk_id", ""))
        return {"statusCode": 200, "body": json.dumps({"message": "Node added"})}
    return {"statusCode": 400, "body": json.dumps({"error": "Unknown action"})}