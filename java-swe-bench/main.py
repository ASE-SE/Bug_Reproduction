"""
main.py
主控调度器。串联整个自动化缺陷复现 Pipeline。
🚀 进阶修复版：加入了“幽灵文件清理”机制，防止大模型多次重试时的多包名残留导致编译错乱。
"""

import json
from pathlib import Path

from constants import logger, REPOS_DIR, RESULTS_DIR, EMBEDDED_API_KEY, DEFAULT_API_URL
from data_loader import fetch_and_clean_dataset
from repo_utils import clone_and_checkout, detect_build_system, cleanup_workspace
from build_and_fix import prime_build_dependencies
from api_client import call_api_generate_java_test
from test_writer import write_test_to_repo
from java_runner import run_generated_test

# 👇 引入我们的独立加速模块 👇
from universal_devops_accelerator import setup_global_mirrors, accelerate_repository

def main():
    if not EMBEDDED_API_KEY:
        logger.critical("API_KEY environment variable is missing!")
        return

    try:
        dataset = fetch_and_clean_dataset()
    except Exception as e:
        logger.critical(f"Failed to initialize dataset: {e}")
        return

    logger.info(f"🚀 Starting STRICT BLACK-BOX Auto-Reproduction Pipeline for {len(dataset)} issues.")

    # 🚀 第 1 步拦截：流水线启动时，设置全局宿主机加速（针对 Maven/Gradle）
    setup_global_mirrors()

    for bug_entry in dataset:
        instance_id = bug_entry["instance_id"]
        repo_fullname = bug_entry["repo"]
        base_commit = bug_entry["base_commit"]
        
        logger.info(f"========== Processing Issue: {instance_id} ==========")
        
        safe_repo_name = repo_fullname.replace("/", "_")
        repo_dir = REPOS_DIR / safe_repo_name
        result_file = RESULTS_DIR / f"{instance_id}.json"
        
        if result_file.exists():
            logger.info(f"⏭️ Skipping {instance_id}, result file already exists.")
            continue

        pipeline_result = {
            "instance_id": instance_id, 
            "repo": repo_fullname,
            "status": "pipeline_failed", 
            "failed_at_step": "init",
            "repair_attempts": 0
        }

        try:
            pipeline_result["failed_at_step"] = "clone_and_checkout"
            if not clone_and_checkout(repo_fullname, repo_dir, base_commit):
                raise RuntimeError("Failed to clone or checkout the repository.")
                
            # 🚀 第 2 步拦截：代码检出后，立刻对项目源码进行物理/智能换源（针对 Ruby/Node 等）
            accelerate_repository(repo_dir)
                
            build_sys = detect_build_system(repo_dir)
            if build_sys == "unknown":
                raise RuntimeError("Could not detect Maven or Gradle build system.")

            pipeline_result["failed_at_step"] = "prime_dependencies"
            prime_build_dependencies(repo_dir, build_sys)
            
            # --- 自愈循环核心变量 ---
            previous_code = None
            compiler_error = None
            final_test_json = None
            final_exec_res = None
            
            MAX_ATTEMPTS = 8
            
            for attempt in range(1, MAX_ATTEMPTS + 1):
                logger.info(f"--- 🔄 LLM Execution Cycle {attempt}/{MAX_ATTEMPTS} ---")
                pipeline_result["repair_attempts"] = attempt - 1
                pipeline_result["failed_at_step"] = "llm_generation"
                
                # 呼叫大脑
                llm_res = call_api_generate_java_test(
                    bug_entry, 
                    repo_dir, 
                    DEFAULT_API_URL, 
                    EMBEDDED_API_KEY,
                    previous_code=previous_code, 
                    compiler_error=compiler_error
                )
                
                if not llm_res.get("ok"):
                    raise RuntimeError(f"LLM Generation Failed: {llm_res.get('error')}")
                    
                test_json = llm_res["data"]
                final_test_json = test_json
                
                # 🧹 核心修复点：写入新代码前，进行“幽灵测试”大扫除
                class_name = test_json.get("class_name")
                if class_name:
                    for ghost_file in repo_dir.rglob(f"{class_name}.java"):
                        try:
                            ghost_file.unlink()
                            logger.info(f"🧹 Cleaned up ghost test file from previous attempt: {ghost_file.relative_to(repo_dir)}")
                        except Exception:
                            pass
                
                # 代码落盘
                pipeline_result["failed_at_step"] = "write_test"
                success, fqn, path = write_test_to_repo(repo_dir, test_json)
                if not success:
                    raise RuntimeError("Failed to correctly write test file to disk.")

                # 执行测试
                pipeline_result["failed_at_step"] = "run_test"
                exec_res = run_generated_test(repo_dir, build_sys, fqn)
                final_exec_res = exec_res
                
                # 判断是否触发编译错误反馈
                if exec_res.get("status") == "compilation_error":
                    logger.warning(f"⚠️ Compilation failed on attempt {attempt}.")
                    previous_code = test_json.get("test_code", "")
                    compiler_error = exec_res.get("stdout_snippet", "")[-4000:] 
                    
                    if attempt < MAX_ATTEMPTS:
                        logger.info("🔧 Injecting compiler feedback into prompt and retrying...")
                        continue 
                    else:
                        logger.error("❌ Max repair attempts reached. Giving up.")
                
                break
                
            # 记录战果
            pipeline_result["llm_generated_data"] = final_test_json
            pipeline_result["execution_result"] = final_exec_res
            if final_exec_res:
                pipeline_result["status"] = final_exec_res.get("status", "unknown")
            pipeline_result["failed_at_step"] = "success"

        except Exception as e:
            logger.error(f"❌ Pipeline aborted for {instance_id} at step [{pipeline_result['failed_at_step']}]: {e}")
            pipeline_result["error_message"] = str(e)
        
        # 结果落盘与环境清理
        try:
            with open(result_file, "w", encoding="utf-8") as f:
                json.dump(pipeline_result, f, indent=2, ensure_ascii=False)
        except Exception as e:
            pass
            
        logger.info(f"========== Finished Issue: {instance_id} | Final Status: {pipeline_result.get('status', 'failed')} ==========\n")

if __name__ == "__main__":
    main()