.q01_group_summary <- function(data) {
  grouped <- split(data, data$evaluator_count)
  rows <- lapply(grouped, function(group) {
    data.frame(
      evaluator_count = group$evaluator_count[[1]],
      episode_n = nrow(group),
      median_score = stats::median(group$quality_score),
      median_oracle_code_score = stats::median(group$oracle_code_score),
      min_score = min(group$quality_score),
      max_score = max(group$quality_score),
      median_wall_seconds = stats::median(group$wall_seconds),
      protocol_success_n = sum(group$protocol_ok),
      stringsAsFactors = FALSE
    )
  })
  do.call(rbind, rows)
}


.q01_quality_plot <- function(data) {
  ggplot2::ggplot(
    data,
    ggplot2::aes(x = evaluator_count, y = quality_score)
  ) +
    ggplot2::geom_line(linewidth = 0.8, colour = nature_colors[[4]]) +
    ggplot2::geom_point(size = 3.2, colour = nature_colors[[1]]) +
    ggplot2::scale_x_continuous(breaks = c(3, 6, 9)) +
    ggplot2::scale_y_continuous(limits = c(0, 100), breaks = seq(0, 100, 20)) +
    ggplot2::labs(
      x = "Requested evaluator subagents",
      y = "End-to-end delivery quality score",
      title = "Completed software delivery in the three-condition pilot"
    ) +
    theme_nature_readable(base_size = 11, legend_position = "none")
}


.q01_cost_plot <- function(data) {
  ggplot2::ggplot(
    data,
    ggplot2::aes(x = evaluator_count, y = wall_seconds)
  ) +
    ggplot2::geom_line(linewidth = 0.8, colour = nature_colors[[4]]) +
    ggplot2::geom_point(size = 3.2, colour = nature_colors[[3]]) +
    ggplot2::scale_x_continuous(breaks = c(3, 6, 9)) +
    ggplot2::expand_limits(y = 0) +
    ggplot2::labs(
      x = "Requested evaluator subagents",
      y = "Episode wall time (seconds)",
      title = "Execution cost in the three-condition pilot"
    ) +
    theme_nature_readable(base_size = 11, legend_position = "none")
}
