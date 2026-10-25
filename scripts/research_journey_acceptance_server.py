"""Isolated UI fixture. Synthetic metadata/model only; never opens the research DB."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    out = Path(args.output).resolve()
    if not out.is_relative_to(root / "output"):
        raise ValueError("QA output must stay inside this repository's output directory")
    out.mkdir(parents=True, exist_ok=True)
    os.environ["YANZHENG_DB_PATH"] = str(out / "bootstrap.db")
    os.environ["YANZHENG_MODEL_PROVIDER"] = "demo"
    sys.path[:0] = [str(root / "webapp"), str(root / "webapp/tests")]
    from app import create_app
    import literature_service
    import workflow
    import lean_service
    from test_direction_proposal import ready
    from test_model_api import FakeModel
    from test_research_journey import search_record
    import uvicorn

    audit = {"import_operations": 0, "search_operations": 0, "model_network_calls": 0}

    class OfflineModel(FakeModel):
        def complete(self, messages):
            project = application.state.store.list()[0]
            self.result = ready()
            sources = workflow.context_sources(project)
            for source in sources:
                notes = workflow._confirmed_notes(project, source["run_id"], source["source_id"])
                if notes:
                    self.result["topics"][0]["evidence"] = [{"source_id": source["id"], "note_id": notes[0]["id"],
                        "relation": "supports", "claim": "QA 合成摘录仅作为背景说明；实际研究价值尚需验证。"}]
                    break
            self.result["revision_note"] = "QA 合成修订：依据确认笔记收窄结论，保留计算预算核查。"
            return super().complete(messages)

    def offline_import(idea, reference):
        audit["import_operations"] += 1
        parsed = literature_service.parse_reference(reference)
        record = search_record(idea, parsed["key"])
        record["import_reference"] = parsed
        source = record["sources"][0]
        source.update(title="[QA 合成题录] 随机低秩近似的研究准备", abstract="QA 合成摘要：比较条件与计算预算需要进一步核查。")
        source["import_metadata"] = {"requested_reference": parsed["key"], "retrieved_at": record["created_at"],
            "response_sha256": hashlib.sha256(b"synthetic-offline-qa-metadata").hexdigest(),
            "scope": "official_metadata_response_only", "caveat": "离线 QA 合成数据，不是真实 API 响应或全文。"}
        return record

    def offline_search(*args):
        audit["search_operations"] += 1
        return search_record(*args)

    literature_service.import_reference = offline_import
    literature_service.search = offline_search
    lean_service.environment = lambda: {"available": False, "reason": "QA disables Lean"}
    model = OfflineModel()
    application = create_app(out / "qa.db", model_client=model)

    @application.get("/__research_qa__")
    def identity():
        return {"database": str(application.state.store.path), "fixture": True, "model_operations": model.calls, **audit}

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    identity_path = out / "SERVER.json"
    identity_path.write_text(json.dumps({"base": f"http://127.0.0.1:{listener.getsockname()[1]}",
                                         "database": str(application.state.store.path), "pid": os.getpid()}), encoding="utf-8")
    try:
        uvicorn.Server(uvicorn.Config(application, log_level="warning")).run(sockets=[listener])
    finally:
        listener.close()


if __name__ == "__main__":
    main()
