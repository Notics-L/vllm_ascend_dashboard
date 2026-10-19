import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import dayjs from "dayjs";
import {
  Alert,
  Button,
  Col,
  Drawer,
  Progress,
  Row,
  Segmented,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
} from "antd";
import {
  Area,
  AreaChart,
  Bar,
  CartesianGrid,
  ComposedChart,
  LabelList,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  getNpuQueueDashboard,
  getNpuQueueTrend,
  getNpuQueueTrendDetail,
  type QueueItem,
  type QueueTrendPod,
  type QueueTrendPoint,
} from "../services/npuQueue";
import {
  getEnabledResourceClusters,
  getNodeUtilizationHeatmap,
  getResourceDashboard,
} from "../services/resourceDashboard";
import { getNpuMetrics } from "../services/resourceMetrics";
import "./NpuQueueDemand.css";

const { Text } = Typography;
const number = (value: number) =>
  value.toLocaleString("zh-CN", { maximumFractionDigits: 1 });
const comparablePoolName = (value: string) =>
  value
    .toLowerCase()
    .replace(/^dev[-_]?/, "")
    .replace(/[^a-z0-9]/g, "");
const heatColor = (value: number | null) => {
  if (value === null) return "repeating-linear-gradient(135deg, #fff, #fff 4px, #e5e7eb 4px, #e5e7eb 6px)";
  if (value === 0) return "#d9d9d9";
  if (value <= 25) return "#eeeafd";
  if (value <= 50) return "#c8bdf7";
  if (value <= 75) return "#8068d9";
  return "#211950";
};
export default function NpuQueueDemand() {
  const [queuePools, setQueuePools] = useState<string[]>([]);
  const [trendPool, setTrendPool] = useState("all");
  const [selectedPoint, setSelectedPoint] = useState<QueueTrendPoint | null>(
    null,
  );
  const [selectedQueue, setSelectedQueue] = useState<QueueItem | null>(null);
  const [poolMode, setPoolMode] = useState<"current" | "usage" | "machines">("current");
  const [poolModeDirection, setPoolModeDirection] = useState<"forward" | "backward">("forward");
  const [sectionPool, setSectionPool] = useState("all");
  const [trendMode, setTrendMode] = useState<"current" | "history">("current");
  const [trendModeDirection, setTrendModeDirection] = useState<"forward" | "backward">("forward");
  const [taskMode, setTaskMode] = useState<"queue" | "cost">("queue");
  const [costPool, setCostPool] = useState("all");
  const [costDays, setCostDays] = useState<1 | 7>(7);
  const [costScopes, setCostScopes] = useState<Array<"actual" | "full">>([
    "actual",
  ]);
  const [expandedPoolKeys, setExpandedPoolKeys] = useState<string[]>([]);
  const queueQuery = useQuery({
    queryKey: ["npu-queue-dashboard"],
    queryFn: () => getNpuQueueDashboard(),
    refetchInterval: 30_000,
  });
  const clusterConfigQuery = useQuery({
    queryKey: ["npu-resource-clusters"],
    queryFn: getEnabledResourceClusters,
    staleTime: 30_000,
  });
  const selectedSectionCluster = clusterConfigQuery.data?.find(
    (cluster) => cluster.name === sectionPool,
  );
  const resourceQuery = useQuery({
    queryKey: ["npu-queue-physical-resource"],
    queryFn: () => getResourceDashboard({ include_pods: true }),
    staleTime: 30_000,
    refetchInterval: 30_000,
  });
  const sectionResourceQuery = useQuery({
    queryKey: ["npu-pool-resource", sectionPool],
    queryFn: () =>
      getResourceDashboard({
        include_pods: true,
        cluster_ids: selectedSectionCluster ? [selectedSectionCluster.id] : undefined,
      }),
    enabled: sectionPool === "all" || Boolean(selectedSectionCluster),
    staleTime: 30_000,
    refetchInterval: 30_000,
  });
  const trendQuery = useQuery({
    queryKey: ["npu-queue-trend", trendPool, trendMode],
    queryFn: () =>
      getNpuQueueTrend(
        trendPool === "all" ? undefined : trendPool,
        trendMode === "current" ? 24 : 168,
      ),
    refetchInterval: 30_000,
  });
  const trendDetailQuery = useQuery({
    queryKey: ["npu-queue-trend-detail", selectedPoint?.capturedAt, trendPool],
    queryFn: () => getNpuQueueTrendDetail(
      selectedPoint!.capturedAt,
      trendPool === "all" ? undefined : trendPool,
    ),
    enabled: Boolean(selectedPoint),
  });
  const usageQuery = useQuery({
    queryKey: ["npu-pool-usage", sectionPool],
    queryFn: () =>
      getNpuMetrics({
        cluster_ids: selectedSectionCluster ? [selectedSectionCluster.id] : undefined,
        time_range: "7d",
      }),
    enabled:
      poolMode === "usage" &&
      (sectionPool === "all" || Boolean(selectedSectionCluster)),
  });
  const heatmapQuery = useQuery({
    queryKey: ["npu-node-utilization-heatmap", sectionPool],
    queryFn: () =>
      getNodeUtilizationHeatmap(sectionPool === "all" ? undefined : sectionPool),
    enabled: poolMode === "machines",
  });
  const currentMachineEnd = useMemo(
    () => dayjs().startOf("minute"),
    [sectionPool, poolMode],
  );
  const currentMachineQuery = useQuery({
    queryKey: ["npu-node-utilization-current", sectionPool, currentMachineEnd.toISOString()],
    queryFn: () =>
      getNodeUtilizationHeatmap(
        sectionPool === "all" ? undefined : sectionPool,
        {
          start: currentMachineEnd.subtract(24, "hour").toISOString(),
          end: currentMachineEnd.toISOString(),
          bucket_hours: 24,
        },
      ),
    enabled: poolMode === "current",
    staleTime: 60_000,
  });
  const costQuery = useQuery({
    queryKey: ["npu-queue-consumption", costPool, costDays],
    queryFn: () =>
      getNpuQueueDashboard({
        consumption_pool: costPool === "all" ? undefined : costPool,
        consumption_days: costDays,
      }),
    enabled: taskMode === "cost",
    staleTime: 30_000,
  });

  const data = useMemo(() => {
    const source = queueQuery.data;
    if (!source) return source;
    return {
      ...source,
      prCosts: [...source.prCosts]
        // A stable ranking makes actual vs. full consumption directly comparable.
        .sort((left, right) => right.allCardHours - left.allCardHours)
        .slice(0, 10),
    };
  }, [queueQuery.data]);
  const pools = useMemo(() => data?.pools ?? [], [data?.pools]);
  const todayQueueSamples = data?.dailyQueue[data.dailyQueue.length - 1]?.samples ?? 0;
  const prCostByBranch = useMemo(
    () => new Map((data?.prCosts ?? []).map((item) => [item.branch, item])),
    [data?.prCosts],
  );
  const trend = useMemo(
    () =>
      [...(trendQuery.data?.points ?? [])]
        .sort((left, right) => dayjs(left.capturedAt).valueOf() - dayjs(right.capturedAt).valueOf())
        .map((point) => ({
          ...point,
          timestamp: dayjs(point.capturedAt).format("MM-DD HH:mm"),
        })),
    [trendQuery.data?.points],
  );
  const dailyPoolUsage = useMemo(() => {
    const totals = new Map<string, number>();
    for (const cluster of usageQuery.data?.clusters ?? []) {
      const metrics = [...cluster.metrics].sort((left, right) =>
        left.collected_at.localeCompare(right.collected_at),
      );
      metrics.forEach((metric, index) => {
        const start = dayjs(metric.collected_at);
        const next = metrics[index + 1]
          ? dayjs(metrics[index + 1].collected_at)
          : start.add(1, "minute");
        const hours = Math.max(0, Math.min(6, next.diff(start, "second") / 3600));
        const date = start.format("MM-DD");
        totals.set(date, (totals.get(date) ?? 0) + metric.npu_used * hours);
      });
    }
    return [...totals.entries()]
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([date, cardHours]) => ({ date, cardHours: Math.round(cardHours * 10) / 10 }));
  }, [usageQuery.data]);
  const totals = useMemo(
    () => ({
      waitingJobs: pools.reduce((sum, pool) => sum + pool.waitingJobs, 0),
      waitingCards: pools.reduce((sum, pool) => sum + pool.waitingCards, 0),
      oldest: Math.max(
        ...(data?.queue ?? []).map((item) => item.waitMinutes),
        0,
      ),
    }),
    [data, pools],
  );
  const queue = useMemo(
    () =>
      (data?.queue ?? []).filter(
        (item) =>
          (queuePools.length === 0 ||
            queuePools.some(
              (poolKey) =>
                pools.find((pool) => pool.key === poolKey)?.name === item.pool,
            )),
      ),
    [data, queuePools, pools],
  );
  const pendingPods = useMemo(
    () =>
      (resourceQuery.data?.clusters ?? []).flatMap((cluster) =>
        cluster.executing_pods
          .filter((pod) => pod.phase === "Pending")
          .map((pod) => ({ ...pod, pool: cluster.cluster_name })),
      ),
    [resourceQuery.data],
  );
  const physicalRows = useMemo(
    () =>
      (sectionResourceQuery.data?.clusters ?? []).map((cluster) => {
        const demand = pools.find(
          (pool) =>
            comparablePoolName(pool.name) ===
            comparablePoolName(cluster.cluster_name),
        );
        const resourceName =
          clusterConfigQuery.data?.find(
            (item) => item.id === cluster.cluster_id,
          )?.npu_resource_name ?? "未配置资源名";
        const waitingCards = demand?.waitingCards ?? 0;
        const pods = cluster.executing_pods;
        const pendingPodDetails = pods.filter((pod) => pod.phase === "Pending");
        const pendingCardsFromDetails = pendingPodDetails.reduce(
          (sum, pod) => sum + pod.requests.npu,
          0,
        );
        const machineRows = (currentMachineQuery.data?.nodes ?? []).filter(
          (node) => node.cluster_id === cluster.cluster_id,
        );
        const machineUtilizations = machineRows.map(
          (node) => node.buckets[node.buckets.length - 1]?.utilization ?? null,
        );
        const knownMachineUtilizations = machineUtilizations.filter(
          (value): value is number => value !== null,
        );
        return {
          key: String(cluster.cluster_id),
          name: cluster.cluster_name,
          capacity: cluster.total.npu,
          runningCards: cluster.used.npu,
          waitingCards,
          idle: cluster.available.npu,
          utilization: cluster.total.npu
            ? Math.round((cluster.used.npu / cluster.total.npu) * 100)
            : null,
          waitingUtilization: cluster.total.npu
            ? Math.min(100, (waitingCards / cluster.total.npu) * 100)
            : 0,
          executingPods: cluster.executing_pods_count,
          // Detail and collapsed summary must use one source. Older persisted
          // responses may contain Pending pods without the newer count fields.
          pendingPods: pendingPodDetails.length,
          pendingCards: pendingCardsFromDetails,
          pods,
          queue: (data?.queue ?? []).filter(
            (item) =>
              comparablePoolName(item.pool) ===
              comparablePoolName(cluster.cluster_name),
          ),
          resourceName,
          error: cluster.error,
          machineUtilizations,
          machineAverage: knownMachineUtilizations.length
            ? Math.round(
              (knownMachineUtilizations.reduce((sum, value) => sum + value, 0) /
                knownMachineUtilizations.length) *
                10,
            ) / 10
            : null,
        };
      }),
    [clusterConfigQuery.data, currentMachineQuery.data?.nodes, data?.queue, pools, sectionResourceQuery.data],
  );
  const costData = useMemo(
    () => [...(costQuery.data?.prCosts ?? [])].sort((left, right) => right.allCardHours - left.allCardHours).slice(0, 10),
    [costQuery.data?.prCosts],
  );
  const costByBranch = useMemo(
    () => new Map(costData.map((item) => [item.branch, item])),
    [costData],
  );
  const physicalCapacity = resourceQuery.data?.overall.total.npu ?? null;
  const physicalRunning = resourceQuery.data?.overall.used.npu ?? null;
  const physicalIdle = resourceQuery.data?.overall.available.npu ?? null;
  const physicalUtilization =
    physicalCapacity && physicalRunning !== null
      ? Math.round((physicalRunning / physicalCapacity) * 100)
      : 0;

  if (queueQuery.isLoading)
    return <div className="npu-demand-loading">正在加载 NPU 队列数据…</div>;
  if (queueQuery.isError || !data)
    return (
      <Alert
        type="error"
        showIcon
        message="NPU 队列数据不可用"
        description="请检查 CI 事实表迁移、采集状态与访问权限。"
      />
    );
  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Row className="npu-demand-focus-row" gutter={[40, 20]}>
        <Col xs={24} lg={12}>
          <section
            className="npu-demand-pressure"
            aria-labelledby="npu-pressure-title"
          >
            <div className="npu-demand-focus-heading">
              <h3 id="npu-pressure-title">01 当前资源压力</h3>
              <Tag color={totals.waitingCards > 0 ? "orange" : "green"}>
                {totals.waitingCards > 0 ? "存在排队压力" : "资源充足"}
              </Tag>
            </div>
            <div
              className="npu-demand-capacity-bar"
              aria-label={
                physicalCapacity === null
                  ? "物理 NPU 资源暂不可用"
                  : `物理 NPU 已运行 ${physicalRunning} 卡，空闲 ${physicalIdle} 卡`
              }
            >
              <span style={{ width: `${physicalUtilization}%` }} />
            </div>
            <div className="npu-demand-capacity-label">
              {physicalCapacity === null ? (
                <span>物理资源快照暂不可用</span>
              ) : (
                <>
                  <span>
                    已运行 <b>{physicalRunning}</b> 卡
                  </span>
                  <span>
                    空闲 <b>{physicalIdle}</b> 卡
                  </span>
                </>
              )}
            </div>
            <div className="npu-demand-pressure-metrics">
              <div>
                <span>当前等待需求</span>
                <strong>
                  {number(totals.waitingCards)} <em>卡</em>
                </strong>
              </div>
              <div>
                <span>排队 Job</span>
                <strong>
                  {number(totals.waitingJobs)} <em>个</em>
                </strong>
              </div>
              <div>
                <span>最长等待</span>
                <strong>
                  {number(totals.oldest)} <em>min</em>
                </strong>
              </div>
            </div>
            <Text className="npu-demand-pressure-note">
              等待需求仅统计 Kubernetes Pending Pod；尚未创建 Pod 的 CI Job
              不计入卡等待。
            </Text>
          </section>
        </Col>
        <Col xs={24} lg={12}>
          <section
            className="npu-demand-delay"
            aria-labelledby="npu-delay-title"
          >
            <div className="npu-demand-focus-heading">
              <h3 id="npu-delay-title">总延迟时长趋势</h3>
              <Text type="secondary">
                计算：GitHub Job 创建（created_at）至开始运行（started_at）；仅已上卡且等待不少于 2 分钟。
              </Text>
            </div>
            <div className="npu-demand-delay-chart">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart
                  data={data.dailyQueue}
                  margin={{ top: 4, right: 34, left: -24, bottom: 0 }}
                >
                  <XAxis
                    dataKey="date"
                    tickLine={false}
                    axisLine={false}
                    fontSize={11}
                    interval={0}
                    tickFormatter={(value: string) => value.slice(5)}
                  />
                  <YAxis
                    tickLine={false}
                    axisLine={false}
                    unit="m"
                    fontSize={11}
                  />
                  <Tooltip />
                  <Line
                    type="monotone"
                    dataKey="p90"
                    stroke="#f59e0b"
                    strokeWidth={2.5}
                    dot={false}
                    connectNulls={false}
                    name="P90"
                  />
                  <Line
                    type="monotone"
                    dataKey="p50"
                    stroke="#3b82f6"
                    strokeWidth={2.5}
                    dot={false}
                    connectNulls={false}
                    name="P50"
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
            <div className="npu-demand-delay-metrics">
              <span>
                P50{" "}
                <b>
                  {data.queueDelaySummary.p50 === null
                    ? "—"
                    : `${number(data.queueDelaySummary.p50)} min`}
                </b>
              </span>
              <span>
                P90{" "}
                <b>
                  {data.queueDelaySummary.p90 === null
                    ? "—"
                    : `${number(data.queueDelaySummary.p90)} min`}
                </b>
              </span>
              <span>
                已上卡样本 <b>{number(data.queueDelaySummary.samples)} 个</b>
              </span>
              {todayQueueSamples === 0 && <span>今日暂无已上卡样本</span>}
            </div>
          </section>
        </Col>
      </Row>

      <section
        className="npu-demand-pool-section"
        aria-labelledby="npu-pool-title"
      >
        <div className="npu-demand-section-heading">
          <div>
            <h2 id="npu-pool-title">02 各资源池资源状态</h2>
            <Text type="secondary">
              Kubernetes 实时资源快照；Pending Pod 单独展示为等待需求。
            </Text>
          </div>
          <Space wrap>
            <Select
              value={sectionPool}
              style={{ minWidth: 180 }}
              onChange={(value) => {
                setSectionPool(value);
                setExpandedPoolKeys([]);
              }}
              options={[
                { value: "all", label: "全部资源池" },
                ...(clusterConfigQuery.data ?? []).map((cluster) => ({
                  value: cluster.name,
                  label: cluster.name,
                })),
              ]}
            />
            <Segmented
              value={poolMode}
              onChange={(value) => {
                const modes = ["current", "usage", "machines"]
                const next = value as "current" | "usage" | "machines"
                setPoolModeDirection(modes.indexOf(next) < modes.indexOf(poolMode) ? "backward" : "forward")
                setPoolMode(next)
              }}
              options={[
                { label: "当前资源", value: "current" },
                { label: "资源消耗", value: "usage" },
                { label: "机器使用率", value: "machines" },
              ]}
            />
          </Space>
        </div>
        <div className={`npu-demand-section-scroll is-${poolModeDirection}`} key={poolMode}>
        {poolMode === "current" ? (
          <Table
            rowKey="key"
            className="npu-demand-pool-table"
            size="middle"
            pagination={false}
            loading={sectionResourceQuery.isLoading}
            locale={{
              emptyText: sectionResourceQuery.isError
                ? "Kubernetes 资源快照获取失败"
                : "暂无已配置的 Kubernetes 资源池",
            }}
            dataSource={physicalRows}
            expandable={{
              expandedRowKeys: expandedPoolKeys,
              onExpandedRowsChange: (keys) =>
                setExpandedPoolKeys(keys.map(String)),
          expandIconColumnIndex: 5,
              expandIcon: ({ expanded, onExpand, record }) => (
                <button
                  type="button"
                className={`npu-occupancy-expand ${expanded ? "is-expanded" : ""}`}
                  aria-label={
                    expanded
                      ? `收起 ${record.name} 明细`
                      : `展开 ${record.name} 明细`
                  }
                  onClick={(event) => onExpand(record, event)}
                >
                  <span className="npu-occupancy-expand-dot" />
                  <span className="npu-occupancy-expand-arrow" />
                </button>
              ),
              expandedRowRender: (record) => (
                <div className="npu-demand-pool-details">
                  <div>
                    <h4>当前占用 Pod</h4>
                    <Text type="secondary">资源名：{record.resourceName}</Text>
                    {record.pods.length ? (
                      <div className="npu-demand-pod-list">
                        <div className="npu-demand-pod-head">
                          <span>Pod</span>
                          <span>命名空间</span>
                          <span>状态</span>
                          <span>申请卡数</span>
                          <span>关联链接</span>
                        </div>
                        {record.pods.map((pod) => (
                          <div
                            className="npu-demand-pod-row"
                            key={`${pod.namespace}/${pod.name}`}
                          >
                            <strong>{pod.name}</strong>
                            <span>{pod.namespace || "—"}</span>
                            <span>{pod.phase || "Unknown"}</span>
                            <span>申请 {number(pod.requests.npu)} 卡</span>
                            <span>
                              {pod.pr_url ? (
                                <a href={pod.pr_url} target="_blank" rel="noreferrer">
                                  PR #{pod.pr_number ?? "详情"}
                                </a>
                              ) : "—"}
                            </span>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <Text type="secondary">暂无执行中 Pod 明细</Text>
                    )}
                  </div>
                  <div>
                    <h4>等待中的 CI Job</h4>
                    {record.queue.length ? (
                      <div className="npu-demand-pod-list">
                        {record.queue.map((item) => (
                          <div className="npu-demand-pod-row" key={item.key}>
                            <strong>{item.job || item.run}</strong>
                            <span>{item.repository}</span>
                            <span>等待 {item.waitMinutes} 分钟</span>
                            <span>申请 {number(item.cards)} 卡</span>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <Text type="secondary">无等待</Text>
                    )}
                  </div>
                </div>
              ),
            }}
            columns={[
              {
                title: "资源池",
                dataIndex: "name",
                key: "name",
                render: (value: string) => <strong>{value}</strong>,
              },
              {
                title: "当前卡占用",
                key: "utilization",
                width: 340,
                render: (_, record) =>
                  record.capacity && record.utilization !== null ? (
                    <div className="npu-demand-utilization">
                      <Progress
                        percent={record.utilization}
                        showInfo={false}
                        size="small"
                        strokeColor="#316dff"
                      />
                      <span>
                        {record.runningCards} / {record.capacity} 卡 ·{" "}
                        {record.utilization}%
                      </span>
                      <div
                        className="npu-demand-waiting-demand"
                        title={`等待需求 ${number(record.waitingCards)} 卡`}
                      >
                        <Progress
                          percent={record.waitingUtilization}
                          showInfo={false}
                          size="small"
                          strokeColor="#f5b642"
                          trailColor="#fff3d8"
                        />
                      </div>
                    </div>
                  ) : (
                    <Text type="secondary">未配置物理容量</Text>
                  ),
              },
              {
                title: "空闲卡",
                dataIndex: "idle",
                key: "idle",
                render: (value: number | null) =>
                  value === null ? "—" : `${value} 卡`,
              },
              {
                title: "运行中 Pod",
                dataIndex: "executingPods",
                key: "executingPods",
                render: (value: number, record) => (
                  <span>
                    {value} 个
                    {record.pendingPods ? (
                      <>
                        <br />
                        <Text type="warning">
                          Pending {record.pendingPods} 个 /{" "}
                          {number(record.pendingCards)} 卡
                        </Text>
                      </>
                    ) : null}
                  </span>
                ),
              },
              {
                title: "机器利用率（近 24h）",
                key: "machineUtilization",
                width: 190,
                render: (_, record) => (
                  <div className="npu-demand-machine-summary">
                    <span>{record.machineAverage === null ? "暂无快照" : `平均 ${record.machineAverage}%`}</span>
                    <div className="npu-demand-machine-squares" aria-label="各机器近 24 小时使用率">
                      {record.machineUtilizations.map((value, index) => (
                        <i key={index} aria-hidden="true" style={{ background: heatColor(value) }} />
                      ))}
                    </div>
                  </div>
                ),
              },
            ]}
          />
        ) : poolMode === "usage" ? (
          <div className="npu-demand-single-history">
            <h3>每日资源消耗（卡时）</h3>
            <Text type="secondary">基于 Kubernetes NPU 快照积分计算</Text>
            {usageQuery.isLoading ? (
              <Spin tip="正在计算资源消耗" />
            ) : usageQuery.isError ? (
              <Alert type="error" showIcon message="资源消耗加载失败" />
            ) : dailyPoolUsage.length === 0 ? (
              <Text type="secondary">所选资源池暂无资源消耗快照</Text>
            ) : (
            <ResponsiveContainer width="100%" height={280}>
              <ComposedChart
                data={dailyPoolUsage}
                margin={{ top: 18, right: 18, left: -18, bottom: 0 }}
              >
                <CartesianGrid
                  strokeDasharray="3 3"
                  vertical={false}
                  stroke="#edf0f4"
                />
                <XAxis dataKey="date" tickLine={false} axisLine={false} />
                <YAxis yAxisId="left" tickLine={false} axisLine={false} />
                <Tooltip />
                <Legend />
                <Bar
                  yAxisId="left"
                  dataKey="cardHours"
                  fill="#52c41a"
                  radius={[4, 4, 0, 0]}
                  name="卡时"
                />
              </ComposedChart>
            </ResponsiveContainer>
            )}
          </div>
        ) : (
          <div className="npu-machine-heatmap-wrap">
            <div className="npu-machine-heatmap-summary">
              <strong>机器时间利用率</strong>
              <span>{heatmapQuery.data?.overall_utilization ?? 0}%</span>
              <Text type="secondary">最近 7 天</Text>
            </div>
            {heatmapQuery.isLoading ? (
              <Spin tip="正在计算机器使用率" />
            ) : heatmapQuery.isError ? (
              <Alert type="error" showIcon message="机器使用率加载失败" />
            ) : heatmapQuery.data?.nodes.length ? (
              <div className="npu-machine-heatmap-scroll">
                <div
                  className="npu-machine-heatmap"
                  style={{
                    gridTemplateColumns: `180px repeat(${heatmapQuery.data.nodes[0].buckets.length}, minmax(76px, 1fr))`,
                  }}
                >
                  <div />
                  {heatmapQuery.data.nodes[0].buckets.map((bucket) => (
                    <div className="npu-machine-heatmap-date" key={bucket.start}>
                      {dayjs(bucket.start).format("MM/DD")}
                    </div>
                  ))}
                  {heatmapQuery.data.nodes.flatMap((node) => [
                    <div className="npu-machine-heatmap-node" key={`${node.cluster_id}/${node.node_name}/label`}>
                      <strong>{node.node_name}</strong>
                      <span>{node.cluster_name}</span>
                    </div>,
                    ...node.buckets.map((bucket) => (
                      <div
                        className="npu-machine-heatmap-cell"
                        key={`${node.cluster_id}/${node.node_name}/${bucket.start}`}
                        style={{ background: heatColor(bucket.utilization) }}
                        title={`${node.cluster_name} / ${node.node_name}\n${dayjs(bucket.start).format("YYYY-MM-DD")}\n利用率：${bucket.utilization === null ? "无数据" : `${bucket.utilization}%`}\n活跃：${(bucket.active_seconds / 3600).toFixed(1)}h / ${(bucket.window_seconds / 3600).toFixed(1)}h\n采样覆盖：${Math.round(bucket.coverage * 100)}%`}
                      />
                    )),
                  ])}
                </div>
              </div>
            ) : (
              <Text type="secondary">所选资源池暂无机器快照</Text>
            )}
            <div className="npu-machine-heatmap-legend">
              {[
                ["0%", 0],
                ["0–25%", 20],
                ["25–50%", 40],
                ["50–75%", 60],
                ["75–100%", 80],
                ["无数据", null],
              ].map(([label, value]) => (
                <span key={String(label)}>
                  <i style={{ background: heatColor(value as number | null) }} />
                  {label}
                </span>
              ))}
            </div>
          </div>
        )}
        </div>
      </section>

      <section
        className="npu-demand-trend-section"
        aria-labelledby="npu-running-trend-title"
      >
        <div className="npu-demand-section-heading">
          <div>
            <h2 id="npu-running-trend-title">03 Queue / Running 趋势</h2>
            <Text type="secondary">
              Kubernetes 口径采集 Pending / Running Pod 数与申请卡数。
            </Text>
          </div>
          <Space>
            <Select
              value={trendPool}
              style={{ minWidth: 180 }}
              onChange={(value) => {
                setTrendPool(value);
                setSelectedPoint(null);
              }}
              options={[
                { value: "all", label: "全部资源池" },
                ...(resourceQuery.data?.clusters ?? []).map((cluster) => ({
                  value: cluster.cluster_name,
                  label: cluster.cluster_name,
                })),
              ]}
            />
            <Segmented
              value={trendMode}
              onChange={(value) => {
                const next = value as "current" | "history"
                setTrendModeDirection(next === "current" && trendMode === "history" ? "backward" : "forward")
                setTrendMode(next)
              }}
              options={[
                { label: "当前 / 近 24h", value: "current" },
                { label: "历史", value: "history" },
              ]}
            />
          </Space>
        </div>
        <div className={`npu-demand-section-scroll is-${trendModeDirection}`} key={`${trendPool}:${trendMode}`}>
        {trendMode === "current" ? (
          <>
            <ResponsiveContainer width="100%" height={320}>
              <AreaChart
                data={trend}
                onClick={(state: {
                  activePayload?: Array<{ payload: QueueTrendPoint }>;
                }) =>
                  setSelectedPoint(state?.activePayload?.[0]?.payload ?? null)
                }
              >
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="timestamp" minTickGap={28} />
                <YAxis />
                <Tooltip />
                <Legend />
                <Area
                  type="monotone"
                  dataKey="waitingCards"
                  name="等待卡数"
                  stackId="cards"
                  stroke="#cf1322"
                  fill="#ffccc7"
                />
                <Area
                  type="monotone"
                  dataKey="runningCards"
                  name="运行卡数"
                  stackId="cards"
                  stroke="#1677ff"
                  fill="#bae0ff"
                />
              </AreaChart>
            </ResponsiveContainer>
            <Text type="secondary">
              最近 24h；点击图中的时间点，可查看对应时刻的排队与运行明细。
            </Text>
          </>
        ) : (
          <div className="npu-demand-single-history">
            <ResponsiveContainer width="100%" height={280}>
              <LineChart
                key={`${trendPool}:${trendMode}:${trend[0]?.capturedAt ?? "empty"}`}
                data={trend}
                margin={{ top: 18, right: 18, left: -18, bottom: 0 }}
                onClick={(state: {
                  activePayload?: Array<{ payload: QueueTrendPoint }>;
                }) =>
                  setSelectedPoint(state?.activePayload?.[0]?.payload ?? null)
                }
              >
                <CartesianGrid
                  strokeDasharray="3 3"
                  vertical={false}
                  stroke="#edf0f4"
                />
                <XAxis dataKey="timestamp" tickLine={false} axisLine={false} minTickGap={32} />
                <YAxis tickLine={false} axisLine={false} />
                <Tooltip />
                <Legend />
                <Line
                  type="monotone"
                  dataKey="waitingCards"
                  stroke="#cf1322"
                  strokeWidth={2.5}
                  dot={false}
                  animationBegin={0}
                  animationDuration={500}
                  animationEasing="linear"
                  name="等待卡数"
                />
                <Line
                  type="monotone"
                  dataKey="runningCards"
                  stroke="#1677ff"
                  strokeWidth={2.5}
                  dot={false}
                  animationBegin={0}
                  animationDuration={500}
                  animationEasing="linear"
                  name="运行卡数"
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
        </div>
      </section>

      <section
        className="npu-demand-task-section"
        aria-labelledby="npu-task-title"
      >
        <div className="npu-demand-section-heading">
          <div>
            <h2 id="npu-task-title">04 任务与资源需求分析</h2>
          </div>
          <Segmented
            value={taskMode}
            onChange={(value) => setTaskMode(value as "queue" | "cost")}
            options={[
              { label: "当前排队", value: "queue" },
              { label: "按 Run 消耗", value: "cost" },
            ]}
          />
        </div>
        {taskMode === "queue" ? (
          <>
            <div className="npu-demand-queue-filter">
              <Select
                mode="multiple"
                allowClear
                value={queuePools}
                onChange={setQueuePools}
                placeholder="全部资源池"
                options={pools.map((pool) => ({
                  value: pool.key,
                  label: pool.name,
                }))}
              />
            </div>
            {pendingPods.length && queue.length === 0 ? (
              <>
                <Text strong>Kubernetes 等待调度 Pod</Text>
                <Text
                  type="secondary"
                  style={{ display: "block", margin: "4px 0 10px" }}
                >
                  来自资源快照；当前筛选条件下没有匹配的 Pending Pod。
                </Text>
                <Table
                  rowKey={(pod) =>
                    `${pod.cluster_id}/${pod.namespace}/${pod.name}`
                  }
                  className="npu-demand-queue-table"
                  dataSource={pendingPods}
                  pagination={false}
                  columns={[
                    {
                      title: "Pod",
                      dataIndex: "name",
                      render: (value: string) => <strong>{value}</strong>,
                    },
                    { title: "资源池", dataIndex: "pool", width: 180 },
                    { title: "命名空间", dataIndex: "namespace", width: 160 },
                    {
                      title: "状态",
                      dataIndex: "phase",
                      width: 110,
                      render: () => <Tag color="processing">Pending</Tag>,
                    },
                    {
                      title: "申请卡数",
                      key: "cards",
                      width: 130,
                      align: "right",
                      render: (_, pod) => (
                        <strong>{number(pod.requests.npu)} 卡</strong>
                      ),
                    },
                    {
                      title: "创建时间",
                      dataIndex: "created_at",
                      width: 190,
                      render: (value: string | null | undefined) =>
                        value || "—",
                    },
                    {
                      title: "关联链接",
                      key: "link",
                      width: 120,
                      render: (_, pod) => pod.pr_url ? (
                        <a href={pod.pr_url} target="_blank" rel="noreferrer">
                          PR #{pod.pr_number ?? "详情"}
                        </a>
                      ) : "—",
                    },
                  ]}
                />
                <div style={{ height: 22 }} />
              </>
            ) : null}
            <Text strong>Kubernetes Pending Pod</Text>
            <Table<QueueItem>
              rowKey="key"
              className="npu-demand-queue-table"
              dataSource={queue}
              pagination={false}
              locale={{ emptyText: "当前没有处于 Pending 状态的匹配 Pod" }}
              columns={[
                {
                  title: "等待时长",
                  dataIndex: "waitMinutes",
                  width: 150,
                  defaultSortOrder: "descend",
                  sorter: (a, b) => b.waitMinutes - a.waitMinutes,
                  render: (value: number, record) => (
                    <Tag color={record.state === "short" ? "default" : "red"}>
                      {value} 分钟{record.state === "short" ? " · 短暂" : ""}
                    </Tag>
                  ),
                },
                {
                  title: "命名空间",
                  dataIndex: "repository",
                  width: 250,
                  render: (value: string) => (
                    <Text type="secondary">{value}</Text>
                  ),
                },
                {
                  title: "申请卡数",
                  dataIndex: "cards",
                  width: 130,
                  align: "right",
                  render: (value: number) => <strong>{value} 卡</strong>,
                },
                {
                  title: "等待资源池",
                  dataIndex: "pool",
                  render: (value: string) => <strong>{value}</strong>,
                },
                {
                  title: "",
                  key: "detail",
                  width: 100,
                  render: (_, record) => (
                    <Button
                      type="link"
                      size="small"
                      onClick={() => setSelectedQueue(record)}
                    >
                      查看详情
                    </Button>
                  ),
                },
              ]}
            />
          </>
        ) : (
          <div className="npu-demand-cost-view">
            <div className="npu-demand-cost-toolbar">
              <Text type="secondary">
                按 GitHub Actions Run 聚合资源消耗；关联 PR 时一并展示。成功卡时仅统计成功 Job，总卡时包含成功、失败和取消的已完成 Job。
              </Text>
              <div className="npu-demand-cost-toggle">
                <button
                  type="button"
                  aria-pressed={costScopes.includes("actual")}
                  className={`npu-demand-scope-actual ${costScopes.includes("actual") ? "is-active" : ""}`}
                  onClick={() =>
                    setCostScopes((scopes) =>
                      scopes.includes("actual")
                        ? scopes.length > 1
                          ? scopes.filter((scope) => scope !== "actual")
                          : scopes
                        : [...scopes, "actual"],
                    )
                  }
                >
                  <span className="npu-demand-scope-dot" aria-hidden="true" />
                  成功 Job 卡时
                </button>
                <button
                  type="button"
                  aria-pressed={costScopes.includes("full")}
                  className={`npu-demand-scope-full ${costScopes.includes("full") ? "is-active" : ""}`}
                  onClick={() =>
                    setCostScopes((scopes) =>
                      scopes.includes("full")
                        ? scopes.length > 1
                          ? scopes.filter((scope) => scope !== "full")
                          : scopes
                        : [...scopes, "full"],
                    )
                  }
                >
                  <span className="npu-demand-scope-dot" aria-hidden="true" />
                  全部 Job 尝试卡时
                </button>
              </div>
            </div>
            <div className="npu-demand-cost-filter">
              <Select
                value={costPool}
                style={{ minWidth: 180 }}
                onChange={setCostPool}
                options={[
                  { value: "all", label: "全部资源池" },
                  ...(clusterConfigQuery.data ?? []).map((cluster) => ({ value: cluster.name, label: cluster.name })),
                ]}
              />
              <Segmented
                value={costDays}
                onChange={(value) => setCostDays(value as 1 | 7)}
                options={[{ label: "最近 7 天", value: 7 }, { label: "最近 1 天", value: 1 }]}
              />
              <Text type="secondary">按总卡时排序，展示前 10 条 Run</Text>
            </div>
            <Text type="secondary" style={{ display: "block", marginBottom: 10 }}>
              {costQuery.data?.usagePeriod?.startDate
                ? `数据范围：${data.usagePeriod.startDate} 至 ${data.usagePeriod.endDate}`
                : "暂无已完成的匹配 CI Job。"}
            </Text>
            <ResponsiveContainer width="100%" height={320}>
              <ComposedChart
                layout="vertical"
                data={costData}
                margin={{ top: 8, right: 58, left: 12, bottom: 8 }}
                barGap={1}
                barCategoryGap="35%"
              >
                <CartesianGrid
                  strokeDasharray="3 3"
                  horizontal={false}
                  stroke="#d9e0ea"
                />
                <XAxis
                  type="number"
                  tickLine={false}
                  axisLine={{ stroke: "#98a2b3" }}
                  label={{
                    value: "卡时",
                    position: "insideBottomRight",
                    offset: -2,
                    fill: "#667085",
                    fontSize: 12,
                  }}
                />
                <YAxis
                  type="category"
                  dataKey="branch"
                  width={260}
                  tickLine={false}
                  axisLine={{ stroke: "#98a2b3" }}
                  tick={({ x, y, payload }: { x: number; y: number; payload: { value: string } }) => {
                    const record = costByBranch.get(payload.value);
                    const text = <text x={x} y={y} textAnchor="end" fill={record?.detailUrl ? "#635bff" : "#475467"} fontSize={12}>{payload.value}</text>;
                    return record?.detailUrl ? (
                      <a href={record.detailUrl} target="_blank" rel="noreferrer">{text}</a>
                    ) : text;
                  }}
                />
                <Tooltip content={({ active, payload }: any) => {
                  const record = payload?.[0]?.payload;
                  if (!active || !record) return null;
                  return <div className="npu-demand-tooltip">
                    <div>Run: {record.runDisplayName || record.branch}</div>
                    {record.prNumber ? <div>PR: #{record.prNumber}</div> : record.event ? <div>Trigger: {record.event}</div> : <div>Ref: {record.triggerScope || "—"}</div>}
                    <div>成功 Job 卡时: {number(record.cardHours)} 卡时</div>
                    <div>全部 Job 尝试卡时: {number(record.allCardHours)} 卡时</div>
                  </div>;
                }} />
                {costScopes.includes("actual") && (
                  <Bar
                    dataKey="cardHours"
                    name="成功 Job 卡时"
                    fill="#1677ff"
                    barSize={8}
                    radius={[0, 4, 4, 0]}
                  >
                    <LabelList
                      dataKey="cardHours"
                      position="right"
                      formatter={(value: number) =>
                        value ? number(value) : ""
                      }
                      fill="#1677ff"
                      fontSize={11}
                    />
                  </Bar>
                )}
                {costScopes.includes("full") && (
                  <Bar
                    dataKey="allCardHours"
                    name="全部 Job 尝试卡时"
                    fill="#722ed1"
                    barSize={8}
                    radius={[0, 4, 4, 0]}
                  >
                    <LabelList
                      dataKey="allCardHours"
                      position="right"
                      formatter={(value: number) =>
                        value ? number(value) : ""
                      }
                      fill="#722ed1"
                      fontSize={11}
                    />
                  </Bar>
                )}
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        )}
      </section>

      <Drawer
        title={
          selectedPoint
            ? `队列时点明细 · ${dayjs(
                trendDetailQuery.data?.capturedAt ?? selectedPoint.capturedAt,
              ).format("YYYY-MM-DD HH:mm")}`
            : "队列时点明细"
        }
        open={Boolean(selectedPoint)}
        onClose={() => setSelectedPoint(null)}
        width={760}
      >
        {selectedPoint && trendDetailQuery.isLoading && (
          <div style={{ padding: 48, textAlign: "center" }}>
            <Spin tip="正在加载历史快照" />
          </div>
        )}
        {selectedPoint && trendDetailQuery.isError && (
          <Alert
            type="error"
            showIcon
            message="历史快照加载失败"
            description="该时点的 Kubernetes 资源快照不可用，请稍后重试。"
          />
        )}
        {selectedPoint && trendDetailQuery.data && (
          <Space direction="vertical" size="large" style={{ width: "100%" }}>
            <Alert
              type="info"
              showIcon
              message={`${dayjs(trendDetailQuery.data.capturedAt).format("YYYY-MM-DD HH:mm")}：等待 ${trendDetailQuery.data.waitingCards} 卡 / ${trendDetailQuery.data.waitingJobs} Pod；运行 ${trendDetailQuery.data.runningCards} 卡 / ${trendDetailQuery.data.runningJobs} Pod`}
            />
            <Table<QueueTrendPod>
              rowKey={(pod) =>
                `${pod.cluster_id}/${pod.namespace ?? ""}/${pod.name}`
              }
              dataSource={trendDetailQuery.data.pods}
              pagination={false}
              columns={[
                { title: "Pod", dataIndex: "name" },
                { title: "资源池", dataIndex: "cluster_name" },
                { title: "命名空间", dataIndex: "namespace" },
                {
                  title: "状态",
                  dataIndex: "phase",
                  render: (value) => (
                    <Tag color={value === "Pending" ? "warning" : "blue"}>
                      {value}
                    </Tag>
                  ),
                },
                { title: "卡数", dataIndex: "npu", align: "right" },
              ]}
            />
          </Space>
        )}
      </Drawer>
      <Drawer
        title={
          selectedQueue ? `排队任务详情 · ${selectedQueue.job}` : "排队任务详情"
        }
        open={Boolean(selectedQueue)}
        onClose={() => setSelectedQueue(null)}
        width={560}
      >
        {selectedQueue && (
          <Space direction="vertical" size="middle" style={{ width: "100%" }}>
            <div className="npu-demand-detail-grid">
              <Text type="secondary">命名空间</Text>
              <strong>{selectedQueue.repository}</strong>
              <Text type="secondary">等待时长</Text>
              <strong>{selectedQueue.waitMinutes} 分钟</strong>
              <Text type="secondary">申请卡数</Text>
              <strong>{selectedQueue.cards} 卡</strong>
              <Text type="secondary">等待资源池</Text>
              <strong>{selectedQueue.pool}</strong>
              <Text type="secondary">Run</Text>
              <span>{selectedQueue.run}</span>
              <Text type="secondary">创建时间</Text>
              <span>{selectedQueue.createdAt}</span>
            </div>
            <div>
              <Text strong>PR / 标题</Text>
              <br />
              <Text>{selectedQueue.title}</Text>
            </div>
          </Space>
        )}
      </Drawer>
    </Space>
  );
}
