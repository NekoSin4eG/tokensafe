import json
import os
import shutil
import tempfile
import unittest

from src.schema_slicer import SchemaSlicer


class TestSchemaSlicer(unittest.TestCase):
    """Auto-slicing for heavy declarative schema files (OpenAPI/Prisma/GraphQL/SQL)."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _write(self, name, text):
        p = os.path.join(self.dir, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return p

    def _openapi_spec(self, paths=40):
        schemas = {f"Schema{i}": {"type": "object", "properties": {"f": {"type": "string"}}} for i in range(10)}
        schemas["Address"] = {"type": "object", "properties": {"city": {"type": "string"}}}
        schemas["User"] = {"type": "object", "properties": {"name": {"type": "string"}, "address": {"$ref": "#/components/schemas/Address"}}}
        spec_paths = {}
        for i in range(paths):
            spec_paths[f"/resource{i}"] = {"get": {"operationId": f"getRes{i}", "responses": {"200": {"description": "ok"}}}}
        spec_paths["/users/{id}"] = {"get": {"operationId": "getUser", "responses": {"200": {"description": "ok", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/User"}}}}}}}
        return {"openapi": "3.0.3", "info": {"title": "t"}, "paths": spec_paths, "components": {"schemas": schemas}}

    def test_openapi_json_path_slice_with_ref_closure(self):
        p = self._write("openapi.json", json.dumps(self._openapi_spec()))
        self.assertEqual(SchemaSlicer.detect(p), "openapi")
        sliced, info = SchemaSlicer.slice_file(p, "/users/{id}")
        self.assertIn("/users/{id}", sliced)
        self.assertIn('"User"', sliced)
        self.assertIn("Address", sliced)  # 1-hop $ref closure
        self.assertNotIn("/resource7", sliced)
        self.assertEqual(info["mode"], "path")

    def test_openapi_schema_slice(self):
        p = self._write("openapi.json", json.dumps(self._openapi_spec()))
        sliced, info = SchemaSlicer.slice_file(p, "User")
        self.assertIn('"User"', sliced)
        self.assertIn("Address", sliced)
        self.assertNotIn("/users/{id}", sliced)

    def test_openapi_overview_is_compact_index(self):
        p = self._write("openapi.json", json.dumps(self._openapi_spec()))
        sliced, info = SchemaSlicer.slice_file(p, "")
        self.assertIn("## Paths", sliced)
        self.assertIn("## Schemas", sliced)
        self.assertLess(len(sliced), 4000)  # compact vs the multi-10k-token raw dump

    def test_openapi_yaml_path_slice(self):
        lines = ["openapi: 3.0.3", "paths:"]
        for i in range(20):
            lines.append(f"  /item{i}:")
            lines.append("    get:")
            lines.append("      summary: item")
        lines.append("  /users/{id}:")
        lines.append("    get:")
        lines.append("      summary: user")
        p = self._write("spec.yaml", "\n".join(lines) + "\n")
        self.assertEqual(SchemaSlicer.detect(p), "openapi")
        sliced, info = SchemaSlicer.slice_file(p, "/users/{id}")
        self.assertIn("/users/{id}", sliced)
        self.assertIn("summary: user", sliced)
        self.assertNotIn("/item5", sliced)

    def test_prisma_model_slice_with_one_hop(self):
        prisma = """
model User {
  id    Int     @id
  posts Post[]
}
model Post {
  id       Int    @id
  author   User   @relation(fields: [authorId], references: [id])
  comments Comment[]
  authorId Int
}
model Comment {
  id     Int  @id
  postId Int
}
enum Status {
  ACTIVE
}
"""
        p = self._write("schema.prisma", prisma)
        self.assertEqual(SchemaSlicer.detect(p), "prisma")
        sliced, info = SchemaSlicer.slice_file(p, "Post")
        self.assertIn("model Post {", sliced)
        self.assertIn("model User {", sliced)  # 1-hop via relation field type
        self.assertIn("model Comment {", sliced)
        self.assertNotIn("enum Status", sliced)

    def test_graphql_type_slice_with_one_hop(self):
        gql = """
type Query { user(id: ID!): User }
type User { posts: [Post!]! name: String }
type Post { author: User! title: String }
input PostInput { title: String }
"""
        p = self._write("schema.graphql", gql)
        self.assertEqual(SchemaSlicer.detect(p), "graphql")
        sliced, info = SchemaSlicer.slice_file(p, "Post")
        self.assertIn("type Post {", sliced)
        self.assertIn("type User {", sliced)
        self.assertNotIn("input PostInput", sliced)

    def test_sql_ddl_only_drops_dml(self):
        stmts = [
            "CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT);",
            "CREATE TABLE logs (id INTEGER, msg TEXT);",
        ]
        inserts = "\n".join([f"INSERT INTO logs VALUES ({i}, 'line {i}');" for i in range(200)])
        p = self._write("migrations.sql", "\n".join(stmts) + "\n" + inserts + "\n")
        self.assertEqual(SchemaSlicer.detect(p), "sql")
        overview, info = SchemaSlicer.slice_file(p, "")
        self.assertIn("users", overview)
        self.assertNotIn("INSERT", overview)
        sliced, info = SchemaSlicer.slice_file(p, "users")
        self.assertIn("CREATE TABLE users", sliced)
        self.assertNotIn("CREATE TABLE logs", sliced)
        self.assertNotIn("INSERT", sliced)

    def test_detect_ignores_regular_code_and_json(self):
        plain = self._write("plain.json", json.dumps({"hello": "world"}))
        self.assertIsNone(SchemaSlicer.detect(plain))
        pyf = self._write("mod.py", "def x():\n    pass\n")
        self.assertIsNone(SchemaSlicer.detect(pyf))

    def test_mcp_smart_read_file_routes_schema_files(self):
        from src.mcp_server import _dispatch_tool_call
        p = self._write("openapi.json", json.dumps(self._openapi_spec()))
        res = _dispatch_tool_call("smart_read_file", {"path": p, "symbol": "/users/{id}"})
        text = res["content"][0]["text"]
        self.assertIn("Schema Slice", text)
        self.assertIn("/users/{id}", text)
        self.assertNotIn("/resource7", text)


if __name__ == "__main__":
    unittest.main()
