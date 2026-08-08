# 目的：读取每个 sealed episode 的外部 oracle 评分，生成完整分析数据与描述性统计
# 输入：../results/pilot.csv
# 参数：三臂各 1 次；不应用事后筛选阈值
# 输出：tmp/quality_analysis/processed_data.rds 与 computation_results.rds

source("00.Environment.R")
source("quality_analysis_functions.R")

input_path <- Sys.getenv("LSM_OBSERVATIONS_CSV", unset = file.path("..", "results", "pilot.csv"))
output_dir <- file.path("tmp", "quality_analysis")
dir.create(output_dir, recursive = TRUE, showWarnings = FALSE)
if (!file.exists(input_path)) stop("Missing scored pilot input: ", input_path)

processed_data <- utils::read.csv(input_path, stringsAsFactors = FALSE, check.names = FALSE)
if ("arm_id" %in% names(processed_data)) {
  processed_data$evaluator_count <- as.integer(sub("^evaluators-", "", processed_data$arm_id))
  processed_data$quality_score <- as.numeric(processed_data[["metric.oracle.quality_score"]])
  processed_data$oracle_code_score <- processed_data$quality_score
  processed_data$scorer_status <- ifelse(
    is.na(processed_data[["metric_missing_reason.oracle.quality_score"]]), "ok", "failed"
  )
  processed_data$order_position <- processed_data$ordinal
  processed_data$delivery_completed <- processed_data$episode_status == "completed"
  processed_data$protocol_ok <- processed_data$process_status == "completed" &
    processed_data$protocol_status == "completed" &
    processed_data$capture_status == "completed" &
    processed_data$workspace_status == "completed"
  processed_data$requested_evaluators <- processed_data$evaluator_count
  processed_data$completed_evaluators <- NA_integer_
  processed_data$summary_count <- NA_integer_
  processed_data$executor_count <- 1L
  category_names <- c("api_quality", "basic_ttl", "failure_cancel", "invalidation_race", "lru_capacity", "single_flight")
  for (category in category_names) {
    processed_data[[paste0("score_", category)]] <- as.numeric(
      processed_data[[paste0("metric.oracle.category_", category)]]
    )
  }
}
if (anyDuplicated(processed_data$episode_id)) stop("episode_id must be unique")
if (any(processed_data$scorer_status != "ok")) stop("all pilot oracle runs must succeed")
counts <- table(processed_data$evaluator_count)
if (!identical(as.integer(names(counts)), c(3L, 6L, 9L)) || length(unique(counts)) != 1L) {
  stop("input must contain equally many episodes for evaluator counts 3, 6, and 9")
}

processed_data$protocol_ok <- as.logical(processed_data$protocol_ok)
processed_data$score_per_minute <- processed_data$quality_score / (processed_data$wall_seconds / 60)
processed_data$score_per_1000_tokens <- ifelse(
  is.na(processed_data$total_tokens) | processed_data$total_tokens <= 0,
  NA_real_,
  processed_data$quality_score / (processed_data$total_tokens / 1000)
)

group_summary <- .q01_group_summary(processed_data)
quality_slope <- stats::coef(stats::lm(quality_score ~ evaluator_count, data = processed_data))[[2]]
quality_spearman <- stats::cor(
  processed_data$evaluator_count,
  processed_data$quality_score,
  method = "spearman"
)
best_row <- processed_data[which.max(processed_data$quality_score), , drop = FALSE]
slowest_row <- processed_data[which.max(processed_data$wall_seconds), , drop = FALSE]
computation_results <- list(
  group_summary = group_summary,
  quality_slope = quality_slope,
  quality_spearman = quality_spearman,
  best_row = best_row,
  slowest_row = slowest_row
)

saveRDS(processed_data, file.path(output_dir, "processed_data.rds"))
saveRDS(computation_results, file.path(output_dir, "computation_results.rds"))
utils::write.csv(group_summary, file.path("..", "results", "pilot-summary.csv"), row.names = FALSE)

message("Saved complete episode-level data and descriptive pilot statistics to ", output_dir)
