package org.example;

import com.fasterxml.jackson.databind.*;
import com.fasterxml.jackson.core.type.TypeReference;
import com.github.javaparser.*;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.body.*;
import com.github.javaparser.Range;

import java.nio.file.*;
import java.util.*;
import java.io.*;
import java.time.Duration;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;

/**
 * ExtractMethodsJavaParser (fixed - uses java.net.http.HttpClient and correct Modifier usage)
 *
 * Build:
 *   mvn -q package
 *
 * Run:
 *   java -jar target/extract-methods-javaparser-1.0-SNAPSHOT.jar [jsonPath] [githubToken(optional)] [defaultBranch(optional)]
 *
 * Default JSON path (Windows example) is:
 *   F:\Professional books\software defect\issues_commits_v2_precheck.json
 *
 * Output: extracted_methods_javaparser.json next to input JSON.
 */
public class ExtractMethodsJavaParser {
    static final ObjectMapper MAPPER = new ObjectMapper();
    static final String DEFAULT_JSON = "F:\\Professional books\\software defect\\issues_commits.json";

    public static void main(String[] args) throws Exception {
        String jsonPath = args.length >= 1 ? args[0] : DEFAULT_JSON;
        String githubToken = args.length >= 2 ? args[1] : null;
        String defaultBranch = args.length >= 3 ? args[2] : "main";

        Path jsonFile = Paths.get(jsonPath);
        if (!Files.exists(jsonFile)) {
            System.err.println("ERROR: input JSON not found: " + jsonPath);
            System.exit(2);
        }

        List<Map<String,Object>> data = MAPPER.readValue(jsonFile.toFile(), new TypeReference<List<Map<String,Object>>>(){});
        List<Map<String,Object>> outputs = new ArrayList<>();

        HttpClient httpClient = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(20))
                .build();

        for (Map<String,Object> entry : data) {
            Object patchObj = entry.get("patch");
            if (patchObj == null) continue;
            String patch = patchObj.toString();

            String repo = (String) entry.getOrDefault("repo_fullname", (String) entry.getOrDefault("repo", ""));
            String commit = (String) entry.getOrDefault("commit_sha", defaultBranch);
            String owner = null, repoName = null;
            if (repo != null && repo.contains("/")) {
                String[] rr = repo.split("/");
                owner = rr[0]; repoName = rr[1];
            }

            Map<String,List<Map<String,Object>>> fileHunks = parsePatch(patch);
            Map<String, List<String>> extracted = new LinkedHashMap<>();

            for (Map.Entry<String,List<Map<String,Object>>> fe : fileHunks.entrySet()) {
                String filePath = fe.getKey();
                String sourceCode = null;
                if (owner != null && repoName != null) {
                    try {
                        sourceCode = fetchRawFromGithub(httpClient, owner, repoName, commit, filePath, githubToken);
                    } catch (Exception ex) {
                        sourceCode = null;
                    }
                }

                List<CallableDeclaration<?>> allCallables = new ArrayList<>();
                if (sourceCode != null) {
                    try {
                        CompilationUnit cu = StaticJavaParser.parse(sourceCode);
                        cu.findAll(MethodDeclaration.class).forEach(allCallables::add);
                        cu.findAll(ConstructorDeclaration.class).forEach(allCallables::add);
                    } catch (Exception e) {
                        allCallables.clear();
                    }
                }

                List<String> foundSigs = new ArrayList<>();
                for (Map<String,Object> hunk : fe.getValue()) {
                    int newStart = (Integer) hunk.get("new_start");
                    int newLen = (Integer) hunk.get("new_len");
                    int newEnd = newStart + newLen - 1;

                    String chosenSig = null;
                    if (!allCallables.isEmpty()) {
                        CallableDeclaration<?> best = null;
                        int bestSize = Integer.MAX_VALUE;
                        for (CallableDeclaration<?> cd : allCallables) {
                            Optional<Range> rOpt = cd.getRange();
                            if (!rOpt.isPresent()) continue;
                            Range r = rOpt.get();
                            int s = r.begin.line;
                            int e = r.end.line;
                            if (!(e < newStart || s > newEnd)) {
                                int size = e - s;
                                if (size < bestSize) {
                                    bestSize = size;
                                    best = cd;
                                }
                            }
                        }
                        if (best != null) {
                            chosenSig = signatureFromCallable(best);
                        }
                    }

                    if (chosenSig == null) {
                        @SuppressWarnings("unchecked")
                        List<String> lines = (List<String>) hunk.get("lines");
                        String heuristic = heuristicFromHunk(lines);
                        chosenSig = heuristic != null ? heuristic : "UNKNOWN";
                    }

                    foundSigs.add(chosenSig);
                }

                LinkedHashSet<String> uniq = new LinkedHashSet<>(foundSigs);
                extracted.put(filePath, new ArrayList<>(uniq));
            }

            Map<String,Object> outEntry = new LinkedHashMap<>();
            outEntry.put("issue_number", entry.get("issue_number"));
            outEntry.put("repo", entry.get("repo_fullname"));
            outEntry.put("commit_sha", entry.get("commit_sha"));
            outEntry.put("extracted", extracted);
            outputs.add(outEntry);
        }

        Path outPath = jsonFile.getParent().resolve("extracted_methods_javaparser.json");
        MAPPER.writerWithDefaultPrettyPrinter().writeValue(outPath.toFile(), outputs);
        System.out.println("WROTE: " + outPath.toAbsolutePath().toString());
    }

    // --- helpers ---

    static Map<String,List<Map<String,Object>>> parsePatch(String patch) {
        Map<String,List<Map<String,Object>>> fileHunks = new LinkedHashMap<>();
        String[] lines = patch.split("\\r?\\n");
        String curFile = null;
        for (int i = 0; i < lines.length; i++) {
            String L = lines[i];
            if (L.startsWith("--- ")) {
                if (i + 1 < lines.length && lines[i+1].startsWith("+++ ")) {
                    String path = lines[i+1].substring(4).trim();
                    if (path.startsWith("b/")) path = path.substring(2);
                    curFile = path;
                    fileHunks.putIfAbsent(curFile, new ArrayList<>());
                }
                continue;
            }
            if (L.startsWith("@@ ")) {
                int plusIdx = L.indexOf('+');
                int at2 = L.indexOf("@@", plusIdx);
                if (plusIdx >= 0 && at2 > plusIdx) {
                    String plusPart = L.substring(plusIdx + 1, at2).trim();
                    String[] parts = plusPart.split(",");
                    int newStart = Integer.parseInt(parts[0]);
                    int newLen = parts.length > 1 ? Integer.parseInt(parts[1]) : 1;
                    List<String> hunkLines = new ArrayList<>();
                    int j = i + 1;
                    while (j < lines.length && !lines[j].startsWith("@@ ") && !lines[j].startsWith("--- ")) {
                        hunkLines.add(lines[j]);
                        j++;
                    }
                    i = j - 1;
                    Map<String,Object> h = new LinkedHashMap<>();
                    h.put("new_start", newStart);
                    h.put("new_len", newLen);
                    h.put("lines", hunkLines);
                    if (curFile != null) fileHunks.get(curFile).add(h);
                }
            }
        }
        return fileHunks;
    }

    static String fetchRawFromGithub(HttpClient httpClient, String owner, String repo, String ref, String filePath, String token) {
        String url = String.format("https://raw.githubusercontent.com/%s/%s/%s/%s", owner, repo, ref, filePath);
        try {
            HttpRequest.Builder rb = HttpRequest.newBuilder()
                    .uri(URI.create(url))
                    .timeout(Duration.ofSeconds(20))
                    .GET();
            if (token != null && !token.isEmpty()) {
                rb.header("Authorization", "token " + token);
            }
            HttpRequest req = rb.build();
            HttpResponse<String> resp = httpClient.send(req, HttpResponse.BodyHandlers.ofString());
            if (resp.statusCode() == 200) return resp.body();
            if (token != null && !token.isEmpty()) {
                HttpRequest req2 = HttpRequest.newBuilder().uri(URI.create(url)).timeout(Duration.ofSeconds(20)).GET().build();
                HttpResponse<String> resp2 = httpClient.send(req2, HttpResponse.BodyHandlers.ofString());
                if (resp2.statusCode() == 200) return resp2.body();
            }
        } catch (Exception e) {
            // ignore
        }
        return null;
    }

    static String signatureFromCallable(CallableDeclaration<?> m) {
        StringBuilder sb = new StringBuilder();
        m.getModifiers().forEach(mod -> {
            // use modifier keyword's asString() to be compatible
            sb.append(mod.getKeyword().asString()).append(" ");
        });
        if (m instanceof MethodDeclaration) {
            MethodDeclaration md = (MethodDeclaration) m;
            sb.append(md.getType().asString()).append(" ");
            sb.append(md.getNameAsString());
        } else if (m instanceof ConstructorDeclaration) {
            sb.append(((ConstructorDeclaration) m).getNameAsString());
        } else {
            sb.append(m.getNameAsString());
        }
        sb.append("(");
        List<String> params = new ArrayList<>();
        m.getParameters().forEach(p -> params.add(p.getType().asString() + " " + p.getNameAsString()));
        sb.append(String.join(", ", params));
        sb.append(")");
        if (m.getThrownExceptions() != null && !m.getThrownExceptions().isEmpty()) {
            sb.append(" throws ");
            List<String> th = new ArrayList<>();
            m.getThrownExceptions().forEach(t -> th.add(t.asString()));
            sb.append(String.join(", ", th));
        }
        return sb.toString().trim();
    }

    static String heuristicFromHunk(List<String> hunkLines) {
        for (int i = hunkLines.size() - 1; i >= 0; i--) {
            String line = hunkLines.get(i).trim();
            if (line.startsWith("+") || line.startsWith("-")) {
                line = line.substring(1).trim();
            }
            if (line.contains("(") && line.contains(")")) {
                String first = line.split("\\s+")[0];
                if (!Arrays.asList("if","for","while","switch","catch","try","else","do","return","new").contains(first)) {
                    if (line.matches(".*\\b(public|protected|private|static|final|synchronized|abstract)\\b.*") ||
                            line.matches("[A-Za-z0-9_\\<\\>\\[\\]]+\\s+[A-Za-z0-9_]+\\s*\\([^)]*\\)\\s*\\{?\\s*$")) {
                        return line;
                    }
                }
            }
        }
        return null;
    }
}
